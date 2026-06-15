"""
Sub-machine adapter for composing specifications.

Provides SubMachine, which maps field names at the state/context boundary
so that sub-spec actions (e.g., channel.send) read/write the parent spec's
fields transparently.  This is the Python analog of TLA+'s INSTANCE ... WITH.

Example::

    InChan = SubMachine[ChannelState](Data="Message", chan="cin")
    channel.send(InChan(c), d)           # channel sees chan → reads/writes cin
    channel.type_invariant(InChan.view(s))  # read-only view for invariants
"""

from __future__ import annotations

from typing import Generic, TypeVar, cast

from wunderspec.expr import Expr, UpdatesBuilder
from wunderspec.machine import Context, StateUpdatesBuilder, machine_param, machine_var

# The sub-spec's state type. Parameterize the factory with it
# (``SubMachine[ChannelState](...)``) so that the wrapped context/view type-check
# against the sub-spec's actions and invariants.
SubState = TypeVar("SubState")


class SubMachine(Generic[SubState]):
    """User-facing factory that maps sub-spec field names to parent-spec names.

    Args:
        **field_map: Mapping from sub-spec name to parent-spec name.
            E.g. ``SubMachine[ChannelState](Data="Message", chan="cin")``.
    """

    def __init__(self, **field_map: str):
        if not field_map:
            raise ValueError("SubMachine requires at least one mapping")
        self._field_map = field_map  # sub_name -> parent_name

    def __call__(self, context: object) -> Context[SubState]:
        """Wrap *context* so that sub-spec actions see mapped field names.

        Typed as the sub-spec's ``Context[SubState]`` so it can be passed to the
        sub-spec's actions; the field-name translation happens at runtime.
        """
        return cast("Context[SubState]", SubMachineContext(context, self._field_map))

    def view(self, state: object) -> SubState:
        """Return a read-only view of *state* with mapped field names.

        Typed as the sub-spec's state so it can be passed to the sub-spec's
        invariants; the field-name translation happens at runtime.
        """
        return cast(SubState, SubMachineStateView(state, self._field_map))


# ---------------------------------------------------------------------------
# SubMachineStateView — read-only proxy for invariants
# ---------------------------------------------------------------------------


class SubMachineStateView:
    """Read-only proxy that translates sub-spec field names to parent fields."""

    __slots__ = ("_backing", "_field_map")

    def __init__(self, backing_state: object, field_map: dict[str, str]):
        object.__setattr__(self, "_backing", backing_state)
        object.__setattr__(self, "_field_map", field_map)

    def __getattr__(self, name: str) -> object:
        field_map: dict[str, str] = object.__getattribute__(self, "_field_map")
        if name not in field_map:
            raise AttributeError(
                f"SubMachineStateView has no field '{name}' "
                f"(mapped fields: {', '.join(field_map)})"
            )
        backing = object.__getattribute__(self, "_backing")
        return getattr(backing, field_map[name])

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SubMachineStateView is read-only")


# ---------------------------------------------------------------------------
# SubMachineState — read/write proxy for actions
# ---------------------------------------------------------------------------


class SubMachineState:
    """Read/write proxy that translates sub-spec field names to parent fields.

    Writes go through the backing state's descriptors so that single-assignment
    tracking and ``__edit_callback`` fire with the *parent* field name.
    """

    def __init__(self, backing_state: object, field_map: dict[str, str]):
        object.__setattr__(self, "_backing", backing_state)
        object.__setattr__(self, "_field_map", field_map)
        # Classify sub-spec names into vars and params based on the backing
        # state's descriptors.
        vars_: list[str] = []
        params_: list[str] = []
        backing_cls = type(backing_state)
        for sub_name, parent_name in field_map.items():
            descriptor = getattr(backing_cls, parent_name, None)
            if isinstance(descriptor, machine_var):
                vars_.append(sub_name)
            elif isinstance(descriptor, machine_param):
                params_.append(sub_name)
            # else: not a recognized descriptor — skip
        object.__setattr__(self, "_vars", tuple(vars_))
        object.__setattr__(self, "_params", tuple(params_))

    # -- read --

    def __getattr__(self, name: str) -> object:
        if name.startswith("_"):
            raise AttributeError(name)
        field_map: dict[str, str] = object.__getattribute__(self, "_field_map")
        if name not in field_map:
            raise AttributeError(
                f"SubMachineState has no field '{name}' "
                f"(mapped fields: {', '.join(field_map)})"
            )
        backing = object.__getattribute__(self, "_backing")
        return getattr(backing, field_map[name])

    # -- write --

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        field_map: dict[str, str] = object.__getattribute__(self, "_field_map")
        if name not in field_map:
            raise AttributeError(
                f"SubMachineState has no field '{name}' "
                f"(mapped fields: {', '.join(field_map)})"
            )
        backing = object.__getattribute__(self, "_backing")
        # Go through setattr so the descriptor __set__ fires (assignment
        # tracking, __edit_callback, etc.) with the parent field name.
        setattr(backing, field_map[name], value)

    # -- editing support --

    def editing(
        self,
        name_prefix: str = "_tmp",
        deferred: bool = True,
    ) -> _SubMachineEditSession:
        backing = object.__getattribute__(self, "_backing")
        field_map: dict[str, str] = object.__getattribute__(self, "_field_map")
        inner = StateUpdatesBuilder(backing, name_prefix, True, deferred)
        builder = SubMachineUpdatesBuilder(inner, field_map, self)
        return _SubMachineEditSession(builder)

    def flush_edits(self) -> None:
        backing = object.__getattribute__(self, "_backing")
        backing.flush_edits()

    def finalize(self) -> None:
        backing = object.__getattribute__(self, "_backing")
        backing.finalize()

    def _asdict(self) -> dict[str, Expr]:
        field_map: dict[str, str] = object.__getattribute__(self, "_field_map")
        backing = object.__getattribute__(self, "_backing")
        return {
            sub_name: getattr(backing, parent_name)
            for sub_name, parent_name in field_map.items()
        }

    def _copy_from(self, other: object) -> None:
        backing = object.__getattribute__(self, "_backing")
        backing._copy_from(other)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# SubMachineUpdatesBuilder — wraps StateUpdatesBuilder with name translation
# ---------------------------------------------------------------------------


class SubMachineUpdatesBuilder:
    """Wraps a ``StateUpdatesBuilder`` on the backing state, translating
    sub-spec field names to parent names before delegating."""

    def __init__(
        self,
        inner: StateUpdatesBuilder,
        field_map: dict[str, str],
        proxy: SubMachineState,
    ):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_field_map", field_map)
        object.__setattr__(self, "_proxy", proxy)

    def __getattr__(self, name: str) -> UpdatesBuilder:
        if name.startswith("_"):
            raise AttributeError(name)
        field_map: dict[str, str] = object.__getattribute__(self, "_field_map")
        proxy: SubMachineState = object.__getattribute__(self, "_proxy")
        vars_ = object.__getattribute__(proxy, "_vars")
        if name not in vars_:
            raise AttributeError(
                f"SubMachineUpdatesBuilder: '{name}' is not a variable "
                f"(variables: {', '.join(vars_)})"
            )
        parent_name = field_map[name]
        inner: StateUpdatesBuilder = object.__getattribute__(self, "_inner")
        return getattr(inner, parent_name)  # type: ignore[no-any-return]

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("Use upd.field[key] = value syntax, not upd.field = value")

    def apply(self) -> None:
        inner: StateUpdatesBuilder = object.__getattribute__(self, "_inner")
        inner.apply()


class _SubMachineEditSession:
    """Context manager wrapper for SubMachineUpdatesBuilder."""

    def __init__(self, builder: SubMachineUpdatesBuilder):
        self._builder = builder

    def __enter__(self) -> SubMachineUpdatesBuilder:
        return self._builder

    def __exit__(self, exc_type: object, exc_value: object, tb: object) -> None:
        if exc_value is None:
            self._builder.apply()


# ---------------------------------------------------------------------------
# SubMachineContext — wraps Context, provides mapped state
# ---------------------------------------------------------------------------


class SubMachineContext:
    """Wraps a ``Context`` and provides a ``SubMachineState`` as ``.state``.

    All context protocol methods delegate to the backing context.  Nested
    ``begin_action`` calls force inlining so that extracted operator bodies
    reference the correct (parent) field names.
    """

    def __init__(self, backing_context: object, field_map: dict[str, str]):
        object.__setattr__(self, "_backing", backing_context)
        object.__setattr__(self, "_field_map", field_map)

    @property
    def state(self) -> SubMachineState:
        backing = object.__getattribute__(self, "_backing")
        field_map: dict[str, str] = object.__getattribute__(self, "_field_map")
        return SubMachineState(backing.state, field_map)

    # -- delegation --

    def one_of(self, base_set: Expr, name: str | None = None) -> object:
        backing = object.__getattribute__(self, "_backing")
        return backing.one_of(base_set, name)

    def alternatives(self, *names: str) -> object:
        backing = object.__getattribute__(self, "_backing")
        return backing.alternatives(*names)

    def assume(self, condition: object) -> None:
        backing = object.__getattribute__(self, "_backing")
        backing.assume(condition)

    def split(self, condition: object) -> object:
        backing = object.__getattribute__(self, "_backing")
        return backing.split(condition)

    def cache(self, expr: Expr, name: str | None = None) -> Expr:
        backing = object.__getattribute__(self, "_backing")
        return backing.cache(expr, name)  # type: ignore[no-any-return]

    def begin_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> tuple[object, ...]:
        """Delegate to backing context, forcing inlining for adapted actions."""
        backing = object.__getattribute__(self, "_backing")
        # Force inlining: save original _inline, set to True, call, restore.
        # This prevents extraction with wrong (parent) field names.
        old_inline = None
        if action_func is not None and hasattr(action_func, "_inline"):
            old_inline = action_func._inline
            action_func._inline = True  # type: ignore[attr-defined]
        try:
            return backing.begin_action(action_func, action_args)  # type: ignore[no-any-return]
        finally:
            if old_inline is not None:
                action_func._inline = old_inline  # type: ignore[union-attr]

    def end_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> None:
        backing = object.__getattribute__(self, "_backing")
        # Force inlining for end_action as well (must match begin_action)
        old_inline = None
        if action_func is not None and hasattr(action_func, "_inline"):
            old_inline = action_func._inline
            action_func._inline = True  # type: ignore[attr-defined]
        try:
            backing.end_action(action_func, action_args)
        finally:
            if old_inline is not None:
                action_func._inline = old_inline  # type: ignore[union-attr]
