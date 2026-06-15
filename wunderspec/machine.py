"""
Primitives for defining symbolic state machines on top of symbolic expressions.

Igor Konnov, 2025-2026
"""

import inspect
from abc import ABC
from enum import Enum
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Callable,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
    get_args,
    get_origin,
    overload,
)

from typing_extensions import Self, TypeAliasType
from typing_extensions import TypeVar as TypeVarExt

from wunderspec.ast.sorts import RecordSort, sort_of
from wunderspec.expr import BoolExpr  # noqa: F401 (used in docstring examples)
from wunderspec.expr import (
    Expr,
    UpdateContext,
    UpdatesBuilder,
    VarExpr,
    _is_record_field_attribute,
    coerce_expr,
    expr_from_node,
)


class StateFieldType(Enum):
    """Type of a state machine field."""

    VAR = "var"
    PARAM = "param"


# we re-export these for convenience
PARAMETER = StateFieldType.PARAM
VARIABLE = StateFieldType.VAR


class UPSERT:
    """Annotation marker for insert-or-update ("upsert") state variables.

    By default, a keyed assignment to a map state variable replaces an existing
    key (``s.field[k] = v`` requires ``k`` to already exist; tools may report an
    error otherwise). Declaring a field as ``StateVar[dict[K, V], UPSERT]`` opts
    it into upsert semantics, so the same assignment inserts ``k`` when missing.
    """


_AnnotationT = TypeVar("_AnnotationT")
_MarkerT = TypeVarExt("_MarkerT", default=Any)


class _AssignableStateExpr(Expr):
    """State variable expression that supports direct, assignment-like updates.

    Reading a state variable returns one of these wrappers. For *reads* it
    behaves exactly like the underlying :class:`Expr` (indexing, slicing,
    unpacking, record-field access, operators). For *writes* it lets you update
    nested structures with plain Python assignment syntax::

        s.x[k] = v            # map / list element
        s.req[p][q] = v       # nested map path
        s.chan.val = v        # record field
        s.cfg[k].val = v      # element, then record field

    Reads never touch the update machinery: ``__getitem__``/record-field access
    return the properly typed sub-expression (wrapped so further assignment still
    works). A write lazily builds a fresh immediate-mode
    :class:`StateUpdatesBuilder` and replays the accumulated path, so the state
    field is updated on the spot and the next access re-reads the new value.
    Nothing is cached on the state, which keeps direct assignment safe across
    ``c.alternatives``/``c.one_of`` branches that snapshot and roll back state.
    """

    def __init__(self, read_expr: Expr, make_builder: Callable[[], Any]):
        super().__init__(read_expr._node)
        # ``_read`` is the properly typed expression used for all read access.
        # ``_make_builder`` lazily produces the path-aware update builder that a
        # write at this position should target (only called on assignment).
        object.__setattr__(self, "_read", read_expr)
        object.__setattr__(self, "_make_builder", make_builder)

    def __getitem__(self, key: object) -> Expr:
        read = object.__getattribute__(self, "_read")
        item: Expr = read[key]  # properly typed read expression
        if isinstance(key, slice):
            # Slices are read-only; assignment to x[a:b] is unsupported anyway.
            return item
        make_builder = object.__getattribute__(self, "_make_builder")
        key_expr = coerce_expr(key)
        return _AssignableStateExpr(item, lambda: make_builder()[key_expr])

    def __setitem__(self, key: object, value: object) -> None:
        make_builder = cast(
            Callable[[], UpdatesBuilder], object.__getattribute__(self, "_make_builder")
        )
        make_builder()[coerce_expr(key)] = value

    def __getattribute__(self, name: str) -> Expr:
        # Record-field access returns a wrapper that reads the field but still
        # routes a later assignment (``s.cfg[k].val = v``) through the builder.
        if _is_record_field_attribute(name):
            try:
                node = object.__getattribute__(self, "_node")
            except AttributeError:
                pass
            else:
                if isinstance(node.sort, RecordSort) and name in node.sort:
                    read = cast(Expr, object.__getattribute__(self, "_read"))
                    make_builder = cast(
                        Callable[[], UpdatesBuilder],
                        object.__getattribute__(self, "_make_builder"),
                    )
                    return _AssignableStateExpr(
                        read[name], lambda: getattr(make_builder(), name)
                    )
        return cast(Expr, object.__getattribute__(self, name))

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        make_builder = cast(
            Callable[[], UpdatesBuilder], object.__getattribute__(self, "_make_builder")
        )
        setattr(make_builder(), name, value)


if TYPE_CHECKING:
    StateVar = TypeAliasType(
        "StateVar",
        Annotated[Expr, _AnnotationT, _MarkerT],
        type_params=(_AnnotationT, _MarkerT),
    )
    Param = TypeAliasType(
        "Param",
        Annotated[Expr, _AnnotationT, PARAMETER],
        type_params=(_AnnotationT,),
    )
else:

    class StateVar:
        """Annotation shorthand for mutable state variables.

        ``StateVar[T]`` is equivalent to ``Annotated[Expr, T]``.
        ``StateVar[T, UPSERT]`` additionally marks the field for upsert
        (insert-or-update) keyed assignments.
        """

        def __class_getitem__(cls, type_hint: object) -> object:
            if isinstance(type_hint, tuple):
                return Annotated[(Expr,) + type_hint]
            return Annotated[Expr, type_hint]

    class Param:
        """Annotation shorthand for state parameters.

        ``Param[T]`` is equivalent to ``Annotated[Expr, T, PARAMETER]``.
        """

        def __class_getitem__(cls, type_hint: object) -> object:
            return Annotated[Expr, type_hint, PARAMETER]


class MachineState(Protocol):
    """Protocol for state classes decorated with @state."""

    _params: tuple[str, ...]
    _vars: tuple[str, ...]

    def _asdict(self) -> dict[str, Expr]: ...

    def finalize(self) -> None:
        """Finalize the state after a transition."""
        ...

    def flush_edits(self) -> None:
        """Flush pending edit assignments to the active context, if any."""
        ...

    def _copy_from(self, other: Self) -> None:
        """Copy all fields from another state instance."""
        ...

    def editing(
        self,
        name_prefix: str = "_tmp",
        deferred: bool = True,
    ) -> "_StateEditSession":
        """Update state fields atomically via a lexical edit session."""
        ...


class MachineStateBase(ABC):
    """
    Base class for state machine states. Inherit from this class when
    using the @state decorator to get proper type checking support.

    The @state decorator adds implementations for all these methods.
    """

    _params: tuple[str, ...]
    _vars: tuple[str, ...]

    def __init__(self, **kwargs: object) -> None:
        """Accept keyword field initialization for static checkers.

        The concrete initializer is installed by @state.
        """
        raise NotImplementedError  # Implemented by @state

    def _asdict(self) -> dict[str, Expr]:
        """Return a dictionary of all field values."""
        raise NotImplementedError  # Implemented by @state

    def finalize(self) -> None:
        """Finalize the state after a transition."""
        raise NotImplementedError  # Implemented by @state

    def _copy_from(self, other: Self) -> None:
        """Copy all fields from another state instance."""
        raise NotImplementedError  # Implemented by @state

    def flush_edits(self) -> None:
        """Flush pending edit assignments to the active context, if any."""
        raise NotImplementedError  # Implemented by @state

    def editing(
        self,
        name_prefix: str = "_tmp",
        deferred: bool = True,
    ) -> "_StateEditSession":
        raise NotImplementedError  # Implemented by @state


_T = TypeVar("_T")
_F = TypeVar("_F", bound=Callable[..., Any])
_P = ParamSpec("_P")


def state(cls: type[_T]) -> type[_T]:
    """
    A class decorator that transforms a class with annotated fields into
    a state machine state class with proper descriptors.

    Fields annotated with `Param[type_hint]` become parameters (using
    `machine_param`), while `StateVar[type_hint]` fields become variables
    (using `machine_var`). The underlying `Annotated[Expr, type_hint]` and
    `Annotated[Expr, type_hint, StateFieldType.PARAM]` forms remain supported.

    The decorator adds:
    - Descriptors for each field
    - `_params` property: tuple of parameter names
    - `_vars` property: tuple of variable names
    - `__init__` that accepts **kwargs of Expr values
    - `__repr__` similar to dataclass
    """
    # Get annotations using inspect.get_annotations (preferred over get_type_hints)
    annotations = inspect.get_annotations(cls, eval_str=True)

    params: list[str] = []
    vars_: list[str] = []

    for field_name, hint in annotations.items():
        # Check if it's an Annotated type
        if get_origin(hint) is Annotated:
            args = get_args(hint)
            # args[0] is Expr, args[1] is the type hint for sort_of, and any
            # remaining args are markers (StateFieldType.PARAM, UPSERT, ...).
            type_hint = args[1] if len(args) > 1 else None
            markers = args[2:]
            is_param = StateFieldType.PARAM in markers
            is_upsert = UPSERT in markers

            descriptor: machine_var | machine_param
            if is_param:
                descriptor = machine_param(type_hint)
                params.append(field_name)
            else:
                descriptor = machine_var(type_hint, replace_only=not is_upsert)
                vars_.append(field_name)

            # Set the descriptor on the class
            descriptor.__set_name__(cls, field_name)
            setattr(cls, field_name, descriptor)

    # Store params and vars as class attributes (tuples for immutability)
    setattr(cls, "_params", tuple(params))
    setattr(cls, "_vars", tuple(vars_))

    # Create __init__ method
    def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
        for name, value in kwargs.items():
            if name not in self._params and name not in self._vars:
                raise TypeError(f"Unknown field: {name}")
            sort = getattr(cls, name).sort
            # make sure that value is an Expr of the correct sort (or can be coerced)
            value_as_expr = coerce_expr(value, sort)
            # set the attribute via the descriptor
            setattr(self, name, value_as_expr)

        # initialize the remaining _params and _vars with Var of proper sort
        uninitialized = []
        for name in self._params + self._vars:
            if name not in kwargs:
                sort = getattr(cls, name).sort
                var_expr = VarExpr(name, sort)
                setattr(self, name, var_expr)
                uninitialized.append(name)

        # Track which fields were not provided by the caller
        self.__dict__["_uninitialized"] = frozenset(uninitialized)

        # reset all variable counters to 0
        _finalize(self)

    # Create __repr__ method
    def __repr__(self):  # type: ignore[no-untyped-def]
        class_name = self.__class__.__name__
        fields = []
        for name in self._params + self._vars:
            if name in self.__dict__:
                fields.append(f"{name}={self.__dict__[name]!r}")
        return f"{class_name}({', '.join(fields)})"

    # Create pretty method for readable, multi-line display
    def pretty(self, max_width: int = 80) -> str:  # type: ignore[no-untyped-def]
        """Pretty print this state, one field per line.

        Each field value is formatted with the AST pretty printer.
        """
        class_name = self.__class__.__name__
        field_strs = []
        for name in self._params + self._vars:
            if name not in self.__dict__:
                continue
            value = self.__dict__[name]
            value_str = value.node.pretty(max_width)
            # Re-indent continuation lines so nested values stay aligned.
            value_str = value_str.replace("\n", "\n  ")
            field_strs.append(f"{name}={value_str}")
        if not field_strs:
            return f"{class_name}()"
        fields_formatted = ",\n  ".join(field_strs)
        return f"{class_name}(\n  {fields_formatted}\n)"

    def _repr_pretty_(self, p, cycle):  # type: ignore[no-untyped-def]
        """IPython pretty printing support."""
        if cycle:
            p.text(f"{self.__class__.__name__}(...)")
        else:
            p.text(self.pretty(max_width=p.max_width))

    def __rich__(self):  # type: ignore[no-untyped-def]
        """rich rendering support (only invoked when rich is installed)."""
        from wunderspec.pretty import to_rich

        return to_rich(self.pretty())

    # Create _asdict method
    def _asdict(self):  # type: ignore[no-untyped-def]
        return {
            name: self.__dict__[name]
            for name in self._params + self._vars
            if name in self.__dict__
        }

    def __copy__(self):  # type: ignore[no-untyped-def]
        new = self.__class__.__new__(self.__class__)
        new.__dict__ = self.__dict__.copy()
        edited_fields = self.__dict__.get("__edit_written_fields")
        if edited_fields is not None:
            new.__dict__["__edit_written_fields"] = set(edited_fields)
        return new

    # Create finalize method
    def _finalize(self):  # type: ignore[no-untyped-def]
        """Finalize the current state by resetting all variable counters to 0."""
        for name in self._vars:
            descriptor = getattr(cls, name)
            if isinstance(descriptor, machine_var):
                descriptor._set_num_assigned(self, 0)
        self.__dict__["__edit_written_fields"] = set()

    # Create _copy_from
    def _copy_from(self, other):  # type: ignore[no-untyped-def]
        """Copy all fields from another state instance."""
        for name in self._params + self._vars:
            if name in other.__dict__:
                self.__dict__[name] = other.__dict__[name]
                descriptor = getattr(cls, name)
                if isinstance(descriptor, machine_var):
                    descriptor._set_num_assigned(
                        self, descriptor._get_num_assigned(other)
                    )
        self.__dict__["__edit_written_fields"] = set(
            other.__dict__.get("__edit_written_fields", set())
        )

    def flush_edits(self):  # type: ignore[no-untyped-def]
        return None

    def editing(  # type: ignore[no-untyped-def]
        self,
        name_prefix: str = "_tmp",
        deferred: bool = True,
    ) -> "_StateEditSession":
        """Get a context manager for atomic, lens-like field updates."""
        return _StateEditSession(
            StateUpdatesBuilder(self, name_prefix, True, deferred=deferred)
        )

    setattr(cls, "__init__", __init__)
    setattr(cls, "__repr__", __repr__)
    setattr(cls, "pretty", pretty)
    setattr(cls, "_repr_pretty_", _repr_pretty_)
    setattr(cls, "__rich__", __rich__)
    setattr(cls, "_asdict", _asdict)
    setattr(cls, "__copy__", __copy__)
    setattr(cls, "finalize", _finalize)
    setattr(cls, "_copy_from", _copy_from)
    setattr(cls, "flush_edits", flush_edits)
    setattr(cls, "editing", editing)

    return cls


class machine_var:
    """
    A descriptor for declaring a state variable of a state machine.

    There are a few rules:
     1. Every state variable has a fixed sort defined at declaration time.
     2. Each variable can be assigned only Expr of the correct sort.
     3. Each variable can be assigned at most once. Once a transition is made,
        the counter must be reset.
    """

    def __init__(self, type_hint, replace_only: bool = True):
        """
        Initialize the state variable descriptor with a type. This type must
        be accepted by `sort_of`.

        ``replace_only`` controls keyed-assignment semantics for map fields:
        when ``True`` (the default) a keyed assignment replaces an existing key;
        when ``False`` (declared via ``StateVar[..., UPSERT]``) it inserts or
        updates the key.
        """
        self.sort = sort_of(type_hint)
        self.replace_only = replace_only

    def __set_name__(self, _owner, name):
        self.name = name
        self._count_key = f"__{name}_cntr"

    def _get_num_assigned(self, instance) -> int:
        result: int = instance.__dict__.get(self._count_key, 0)
        return result

    def _set_num_assigned(self, instance, value: int):
        instance.__dict__[self._count_key] = value

    def __set__(self, instance, value):
        # Coerce Python literals (ints, bools, enum members, ...) to the field
        # sort so that `s.x = 5` works the same as `s.x = Val(5)`.
        if not isinstance(value, Expr):
            value = coerce_expr(value, self.sort)
        if value.sort != self.sort:
            raise TypeError(
                f"State var {self.name} has sort {self.sort}, but got value of sort {value.sort}"
            )
        num_assigned = self._get_num_assigned(instance)
        edited_fields = instance.__dict__.get("__edit_written_fields", set())
        if self.name in edited_fields:
            raise AttributeError(
                f"State var {self.name} has been already assigned on {instance}"
            )
        if num_assigned > 0:
            raise AttributeError(
                f"State var {self.name} has been already assigned on {instance}"
            )
        self._set_num_assigned(instance, num_assigned + 1)
        instance.__dict__[self.name] = value
        # Clear from uninitialized tracking when explicitly set
        uninit = instance.__dict__.get("_uninitialized")
        if uninit is not None and self.name in uninit:
            instance.__dict__["_uninitialized"] = uninit - {self.name}

    def __get__(self, instance, owner) -> Expr | Self:
        if instance is None:
            # accessed on the class, return the descriptor itself
            return self
        # print(f"Get state var {self.name} on {instance}")
        if self.name not in instance.__dict__:
            raise AttributeError(
                f"State var {self.name} has not been set on {instance}"
            )
        result: Expr = instance.__dict__[self.name]
        name = self.name

        def make_builder() -> UpdatesBuilder:
            builder = StateUpdatesBuilder(
                instance, "_tmp", replace_only=True, deferred=False
            )
            field_builder: UpdatesBuilder = getattr(builder, name)
            return field_builder

        return _AssignableStateExpr(result, make_builder)


class machine_param:
    """A descriptor for declaring a parameter of a state machine."""

    def __init__(self, type_hint):
        """
        Initialize the parameter with a type. This type must
        be accepted by `sort_of`.
        """
        self.sort = sort_of(type_hint)

    def __set_name__(self, _owner, name):
        self.name = name

    def __set__(self, instance, value: Expr):
        if not isinstance(value, Expr):
            raise TypeError(
                f"Expected Expr for parameter {self.name}, got {type(value).__name__}"
            )
        if value.sort != self.sort:
            raise TypeError(
                f"Parameter {self.name} has sort {self.sort}, but got value of sort {value.sort}"
            )
        if self.name in instance.__dict__:
            raise AttributeError(
                f"Parameter {self.name} has already been set on {instance}"
            )
        instance.__dict__[self.name] = value
        # Clear from uninitialized tracking when explicitly set
        uninit = instance.__dict__.get("_uninitialized")
        if uninit is not None and self.name in uninit:
            instance.__dict__["_uninitialized"] = uninit - {self.name}

    def __get__(self, instance, owner) -> Expr | Self:
        if instance is None:
            # accessed on the class, return the descriptor itself
            return self
        # print(f"Get parameter {self.name} on {instance}")
        if self.name not in instance.__dict__:
            raise AttributeError(
                f"Parameter {self.name} has not been set on {instance}"
            )
        result: Expr = instance.__dict__[self.name]
        return result


class StateUpdatesBuilder:
    """Builder for lens-like updates on `@state` objects.

    In immediate mode (when `deferred=False`), each field assignment triggers
    the state update instantly. In deferred mode (`deferred=True`), `apply()`
    finalizes all touched top-level fields once.
    """

    def __init__(
        self,
        state_obj: object,
        name_prefix: str,
        replace_only: bool,
        deferred: bool,
    ):
        # use object.__setattr__ since we override __setattr__ below
        object.__setattr__(self, "_state", state_obj)
        object.__setattr__(self, "_name_prefix", name_prefix)
        object.__setattr__(self, "_replace_only", replace_only)
        object.__setattr__(self, "_deferred", deferred)
        object.__setattr__(self, "_field_builders", {})
        object.__setattr__(self, "_field_replacements", {})
        object.__setattr__(self, "_applied", False)

    def __getattr__(self, name: str) -> UpdatesBuilder:
        if name.startswith("_"):
            raise AttributeError(name)
        vars_ = object.__getattribute__(self, "_state")._vars
        if name not in vars_:
            raise AttributeError(f"State has no variable '{name}'")
        replacements: dict[str, Expr] = object.__getattribute__(
            self, "_field_replacements"
        )
        if name in replacements:
            raise AttributeError(
                f"State var {name} has already been assigned in this edit session"
            )
        builders: dict[str, UpdatesBuilder] = object.__getattribute__(
            self, "_field_builders"
        )
        if name not in builders:
            state = object.__getattribute__(self, "_state")
            field_expr = getattr(state, name)
            prefix = object.__getattribute__(self, "_name_prefix")
            # Keyed-assignment semantics are declared per field via the
            # descriptor (StateVar[..., UPSERT]); fall back to the session value
            # for non-descriptor states.
            descriptor = getattr(type(state), name, None)
            if isinstance(descriptor, machine_var):
                replace_only = descriptor.replace_only
            else:
                replace_only = object.__getattribute__(self, "_replace_only")
            deferred = object.__getattribute__(self, "_deferred")

            if deferred:
                on_update = None
            else:

                def on_update(_name: str = name) -> None:
                    _builders: dict[str, UpdatesBuilder] = object.__getattribute__(
                        self, "_field_builders"
                    )
                    _state = object.__getattribute__(self, "_state")
                    ctx = _builders[_name]._ctx
                    new_expr = expr_from_node(ctx.updated_node)
                    descriptor = getattr(type(_state), _name)
                    if (
                        isinstance(descriptor, machine_var)
                        and descriptor._get_num_assigned(_state) > 0
                    ):
                        raise AttributeError(
                            f"State var {_name} has been already assigned on {_state}"
                        )
                    _state.__dict__[_name] = new_expr
                    edited_fields = _state.__dict__.get("__edit_written_fields")
                    if edited_fields is None:
                        edited_fields = set()
                        _state.__dict__["__edit_written_fields"] = edited_fields
                    edited_fields.add(_name)
                    callback = _state.__dict__.get("__edit_callback")
                    if callback is not None:
                        callback(_state, _name, new_expr)
                    uninit = _state.__dict__.get("_uninitialized")
                    if uninit is not None and _name in uninit:
                        _state.__dict__["_uninitialized"] = uninit - {_name}

            ctx = UpdateContext(field_expr, prefix, replace_only, on_update)
            builders[name] = UpdatesBuilder(
                ctx, tuple(), expr_from_node(ctx.updated_node)
            )
        return builders[name]

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        state = object.__getattribute__(self, "_state")
        vars_ = state._vars
        if name not in vars_:
            raise AttributeError(f"State has no variable '{name}'")
        builders: dict[str, UpdatesBuilder] = object.__getattribute__(
            self, "_field_builders"
        )
        if name in builders:
            raise AttributeError(
                f"State var {name} has already been edited by key in this edit session"
            )

        descriptor = getattr(type(state), name)
        value_as_expr = coerce_expr(value, descriptor.sort)
        deferred = object.__getattribute__(self, "_deferred")
        if deferred:
            replacements: dict[str, Expr] = object.__getattribute__(
                self, "_field_replacements"
            )
            replacements[name] = value_as_expr
        else:
            self._assign_field(name, value_as_expr)

    def _assign_field(self, name: str, new_expr: Expr) -> None:
        state = object.__getattribute__(self, "_state")
        descriptor = getattr(type(state), name)
        if (
            isinstance(descriptor, machine_var)
            and descriptor._get_num_assigned(state) > 0
        ):
            raise AttributeError(
                f"State var {name} has been already assigned on {state}"
            )
        state.__dict__[name] = new_expr
        edited_fields = state.__dict__.get("__edit_written_fields")
        if edited_fields is None:
            edited_fields = set()
            state.__dict__["__edit_written_fields"] = edited_fields
        edited_fields.add(name)
        callback = state.__dict__.get("__edit_callback")
        if callback is not None:
            callback(state, name, new_expr)
        uninit = state.__dict__.get("_uninitialized")
        if uninit is not None and name in uninit:
            state.__dict__["_uninitialized"] = uninit - {name}

    def apply(self) -> None:
        """Flush all modified fields to the current action/context."""
        if object.__getattribute__(self, "_applied"):
            return
        builders: dict[str, UpdatesBuilder] = object.__getattribute__(
            self, "_field_builders"
        )
        replacements: dict[str, Expr] = object.__getattribute__(
            self, "_field_replacements"
        )
        state = object.__getattribute__(self, "_state")
        deferred = object.__getattribute__(self, "_deferred")
        callback = state.__dict__.get("__edit_callback")
        for name, builder in builders.items():
            new_expr = expr_from_node(builder._ctx.updated_node)
            if deferred:
                descriptor = getattr(type(state), name)
                if (
                    isinstance(descriptor, machine_var)
                    and descriptor._get_num_assigned(state) > 0
                ):
                    raise AttributeError(
                        f"State var {name} has been already assigned on {state}"
                    )
                state.__dict__[name] = new_expr
                edited_fields = state.__dict__.get("__edit_written_fields")
                if edited_fields is None:
                    edited_fields = set()
                    state.__dict__["__edit_written_fields"] = edited_fields
                edited_fields.add(name)
                if callback is not None:
                    callback(state, name, new_expr)
        for name, new_expr in replacements.items():
            self._assign_field(name, new_expr)
        object.__setattr__(self, "_applied", True)


class _StateEditSession:
    """Context manager wrapper for state.edit()."""

    def __init__(self, builder: StateUpdatesBuilder):
        self._builder = builder

    def __enter__(self) -> StateUpdatesBuilder:
        return self._builder

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_value is None:
            self._builder.apply()


def invariant(func: _F) -> _F:
    """A decorator to mark a function as an invariant of a state machine.

    Currently serves as documentation; no special runtime semantics are attached.
    Sets the ``_is_invariant`` attribute to ``True`` on the decorated function
    so tooling can detect it without importing the module.

    Example:
        @invariant
        def safety(s: MyState) -> BoolExpr:
            return s.x >= Val(0)
    """
    setattr(func, "_is_invariant", True)
    return func


def example(func: _F) -> _F:
    """A decorator to mark a function as an example of a state machine.

    An example is satisfied when the predicate evaluates to true in an explored
    state. Tooling treats it as the dual of ``@invariant``.

    Example:
        @example
        def reaches_one(s: MyState) -> BoolExpr:
            return s.x == Val(1)
    """
    setattr(func, "_is_example", True)
    return func


def temporal(func: _F) -> _F:
    """A decorator to mark a function as a temporal property of a state machine.

    Currently serves as documentation; no special runtime semantics are attached.
    Sets the ``_is_temporal`` attribute to ``True`` on the decorated function
    so tooling can detect it without importing the module.

    Example:
        @temporal
        def liveness(s: MyState):
            return Eventually(s.done)
    """
    setattr(func, "_is_temporal", True)
    return func


def coverage(func: _F) -> _F:
    """A decorator to mark a function as a coverage predicate of a state machine.

    The decorated function must take exactly one argument annotated as
    ``MachineStateBase`` or a subclass thereof, and must declare a return
    type of ``Expr``.

    Sets the ``_is_coverage`` attribute to ``True`` on the decorated function
    so tooling can detect it without importing the module.

    Raises:
        TypeError: if the function does not have exactly one parameter, if
            that parameter is not annotated as a ``MachineStateBase`` subclass,
            or if the return annotation is missing or is not ``Expr``.

    Example:
        @coverage
        def covered_init(s: MyState) -> Expr:
            return 1
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    if len(params) != 1:
        raise TypeError(
            f"@coverage requires exactly one argument, "
            f"but {func.__name__!r} has {len(params)}"
        )
    param = params[0]
    annotation = param.annotation
    if annotation is inspect.Parameter.empty:
        raise TypeError(
            f"@coverage requires the argument to be annotated as a MachineStateBase "
            f"subclass, but {func.__name__!r} has no annotation on "
            f"parameter {param.name!r}"
        )
    if not (isinstance(annotation, type) and issubclass(annotation, MachineStateBase)):
        raise TypeError(
            f"@coverage requires the argument to be a MachineStateBase instance, "
            f"but {func.__name__!r} has annotation {annotation!r} on "
            f"parameter {param.name!r}"
        )
    ret = sig.return_annotation
    if ret is inspect.Parameter.empty:
        raise TypeError(
            f"@coverage requires a return type annotation of 'Expr', "
            f"but {func.__name__!r} has no return annotation"
        )
    if ret is not Expr:
        raise TypeError(
            f"@coverage requires the return type to be 'Expr', "
            f"but {func.__name__!r} has return annotation {ret!r}"
        )
    setattr(func, "_is_coverage", True)
    return func


def instance(func: _F) -> _F:
    """Mark a no-argument function as a state prototype factory.

    The decorated function must take no arguments and return an instance of
    the @state class with all parameters filled in.

    Example:
        @instance
        def two_acceptors() -> FPaxosState:
            return FPaxosState(
                Value=Set(0, 1, 2),
                Acceptor=Set("a1", "a2"),
                ...
            )
    """
    setattr(func, "_is_instance", True)
    return func


def find_instance_factories(module: object) -> list[tuple[str, Callable[..., Any]]]:
    """Find all @instance-decorated factory functions in a module.

    Returns a list of (name, func) pairs for functions where ``_is_instance``
    is True and the function was defined in the module (not imported).
    """
    module_name = getattr(module, "__name__", None)
    results: list[tuple[str, Callable[..., Any]]] = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if getattr(obj, "_is_instance", False) and obj.__module__ == module_name:
            results.append((name, obj))
    return results


@overload
def action(
    func: Callable[_P, Any],
    *,
    inline: bool = True,
    coerce: bool = True,
    init: bool = False,
) -> Callable[_P, Any]: ...


@overload
def action(
    func: None = None,
    *,
    inline: bool = True,
    coerce: bool = True,
    init: bool = False,
) -> Callable[[Callable[_P, Any]], Callable[_P, Any]]: ...


def action(
    func: Callable[..., Any] | None = None,
    *,
    inline: bool = True,
    coerce: bool = True,
    init: bool = False,
) -> Any:
    """A decorator to mark a function as an action of a state machine.

    Args:
        func: The function to decorate. When using @action without parentheses,
            this is the function itself. When using @action(inline=False), this is None.
        inline: If True (default), the action body is inlined at call sites.
            If False, the action is extracted as a separate TLA+ operator
            definition and called by reference.
        init: If True, the TLA+ conversion omits primes on assignments
            (i.e. ``x = 0`` instead of ``x' = 0``).  Use this for
            initialisation actions.

    Example:
        @action(init=True)
        def init(c: Context[State]):
            ...  # assignments rendered without primes in TLA+

        @action
        def next(c: Context[State]):
            ...  # assignments rendered with primes (default)

        @action(inline=False)
        def increment(c: Context[State]):
            ...  # Will be extracted as a separate TLA+ operator
    """

    def decorator(fn: Callable[_P, Any]) -> Callable[_P, Any]:
        @wraps(fn)
        def with_begin_end(*args: Any, **kwargs: Any) -> Any:
            if len(args) == 0:
                raise TypeError("First argument to action must be the context")
            context: Any = args[0]
            # Pass the decorated function and arguments to begin_action
            # Arguments beyond the context are action parameters
            original_args: tuple[object, ...]
            original_kwargs: dict[str, object]
            if coerce:
                original_args = tuple(coerce_expr(a) for a in args[1:])
                original_kwargs = {k: coerce_expr(v) for k, v in kwargs.items()}
            else:
                if any(not isinstance(a, Expr) for a in args[1:]):
                    raise TypeError(
                        "@action(coerce=False) requires non-context arguments to be Expr"
                    )
                if any(not isinstance(v, Expr) for v in kwargs.values()):
                    raise TypeError(
                        "@action(coerce=False) requires non-context keyword arguments to be Expr"
                    )
                original_args = args[1:]
                original_kwargs = kwargs
            # begin_action may return modified args (e.g., VarNodes for parameters)
            modified_args = context.begin_action(with_begin_end, original_args)
            # Call the function with potentially modified arguments
            call_fn = cast(Callable[..., Any], fn)
            result = call_fn(context, *modified_args, **original_kwargs)
            # We do not use try/finally here to let exceptions propagate,
            # as `end_action` may throw additional errors.
            # Pass original args so end_action can build ActionCallNode with them
            context.end_action(with_begin_end, original_args)
            return result

        # Store metadata on the decorated function
        setattr(with_begin_end, "_inline", inline)
        setattr(with_begin_end, "_coerce", coerce)
        setattr(with_begin_end, "_is_init", init)
        setattr(with_begin_end, "_action_name", fn.__name__)
        return cast(Callable[_P, Any], with_begin_end)

    if func is not None:
        # Called as @action without parentheses
        return decorator(func)
    else:
        # Called as @action(inline=False) or @action()
        return decorator


class Alternative(Protocol):
    """A branching alternative, to be resolved by the scheduler."""

    def __init__(self, name: str): ...

    def __enter__(self): ...

    def __exit__(self, exc_type, exc_value, traceback): ...


V = TypeVar("V", covariant=True)
S = TypeVar("S", covariant=True)


class ValueGenerator(Protocol[V]):
    """A context manager for generating values."""

    def __enter__(self) -> V: ...

    def __exit__(self, exc_type, exc_value, traceback): ...


class Context(Protocol[S]):
    """
    A machine context to be used inside actions. This is a protocol,
    so it has different implementations.
    """

    @property
    def state(self) -> S:
        """The state as it is being built by a transition. In the beginning of
        the transition, this is a copy of the current machine state.

        The state enforces a few rules on reading and writing its fields:

        - The action may read and modify this state.
        - Every field of the state may be read multiple times.
        - Every variable field may be assigned only once (including the other
          actions that are called as functions).
        - Parameter fields cannot be assigned.
        """
        ...

    def one_of(self, base_set: Expr, name: str | None = None) -> ValueGenerator[Expr]:
        """Pick an arbitrary value from a set. Different implementations may
        implement different strategies for picking the value:

        - randomized simulation may pick a random value,
        - exhaustive model checking may try all possible values in different runs,
        - symbolic model checking may introduce a fresh symbolic variable constrained
          to be in the set.

        Example:

        ```python
        with c.one_of(Set(3, 4, 5), name="x") as x:
            # use x inside the context
        ```
        """
        ...

    def alternatives(self, *names: str) -> tuple[Alternative, ...]:
        """
        Given a list of alternative names, return a tuple of Alternative
        context managers. The actual context implementation may choose different
        strategies for scheduling the alternatives:

        - randomized simulation may pick one alternative randomly,
        - exhaustive model checking may try all alternatives in different runs,
        - symbolic execution may fork the current path into multiple paths,
          one per alternative,
        - symbolic model checking may introduce a fresh symbolic variable
          to represent the choice of alternative.


        The alternatives must be used as context managers, e.g.,:

        ```python
        (alt1, alt2) = iter(c.alternatives("alt1", "alt2"))
        with alt1:
            # code for alternative 1
        with alt2:
            # code for alternative 2
        ```
        """
        ...

    def assume(self, condition: Expr) -> None:
        """Add an assumption about the current state. Different implementations
        may handle assumptions differently:

        - randomized simulation may check the condition and abort the current
          run if it is false,
        - exhaustive model checking may discard the current successor being
          computed if the condition is false,
        - symbolic model checking and symbolic execution may add the condition
          to the path condition.

        Example:

        ```python
        c.assume(c.state.x > Val(0))
        ```

        """
        ...

    def split(self, condition: Expr) -> tuple[Alternative, Alternative]:
        """
        Do a case split on a boolean condition, returning two alternatives:
        one where the condition holds, and another where it does not hold.

        The `then` alternative adds the assumption that the condition is true,
        while the `else` alternative adds the assumption that the condition is
        false.

        The alternatives must be used as context managers, e.g.,:

        ```python
        (then_, else_) = c.split(condition)
        with then_:
            # code for the then branch
        with else_:
            # code for the else branch
        ```
        """
        ...

    def cache(self, expr: Expr, name: str | None = None) -> Expr:
        """
        Cache an expression by binding it to a name, and return a variable
        expression referencing the cached value.

        This avoids repeated subexpressions in the action AST.
        When the enclosing scope exits (``one_of``, ``alternatives``, ``split``),
        the binding is evicted automatically.

        In ``SymbolicContext``, the binding is translated into an
        ``ActionLetNode`` wrapping the remaining action body.
        In ``ExecContext``, the expression is evaluated immediately and
        the concrete value is returned.

        Example:

        ```python
        q1b = c.cache(s.msgs.filter(lambda m: m.bal == b), name="q1b")
        c.assume(q1b.is_empty.__invert__())
        ```

        Args:
            expr: The expression to cache.
            name: Optional name for the binding. If omitted, a fresh name
                  is generated automatically.

        Returns:
            A ``VarExpr`` (in symbolic mode) or a concrete-value expression
            (in execution mode) representing the cached value.
        """
        ...

    def begin_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> tuple[object, ...]:
        """
        Start an action, performing any necessary validity checks.
        This method is called at the beginning of an action function.
        If your function is annotated with `@action`, you do not have
        to call this method manually.

        Args:
            action_func: The decorated action function (optional). When provided,
                the context can check the _inline attribute to determine if the
                action should be extracted.
            action_args: Arguments passed to the action (beyond the context).

        Returns:
            The (potentially modified) arguments to pass to the action function.
            For non-inline actions, the context may return VarExprs for parameters
            so the action body uses clean parameter names.
        """
        ...

    def end_action(
        self,
        action_func: object | None = None,
        action_args: tuple[object, ...] = (),
    ) -> None:
        """
        Finalize the action, performing any necessary validity checks.
        This method is called at the end of an action function.
        If your function is annotated with `@action`, you do not have
        to call this method manually.

        Args:
            action_func: The decorated action function (optional).
            action_args: The original arguments passed to the action (before
                any modification by begin_action). Used for building ActionCallNode.
        """
        ...


class ControlFlowError(Exception):
    """
    This exception is raised when the control flow in a context
    does not follow the rules, e.g., mixing alternatives incorrectly.

    If you see this exception, check your test scenario and the
    action code for consistency.
    """

    def __init__(self, msg: str):
        super().__init__(msg)


class FixedValueGenerator(ValueGenerator[Expr]):
    """A value generator that yields a predefined value."""

    def __init__(self, predefined_value: Expr):
        self.predefined_value = predefined_value

    def __enter__(self) -> Expr:
        return self.predefined_value

    def __exit__(self, exc_type, exc_value, traceback):
        pass

    def __repr__(self) -> str:
        return f"FixedValueGenerator({repr(self.predefined_value)})"
