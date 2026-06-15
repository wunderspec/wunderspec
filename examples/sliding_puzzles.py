"""
Sliding puzzles (Klotski) in Wunderspec.

Manual translation of:
https://github.com/tlaplus/Examples/blob/master/specifications/SlidingPuzzles/SlidingPuzzles.tla
"""

from typing import Annotated

from wunderspec import *
from wunderspec import StateVar, expr
from wunderspec.machine import Context, MachineStateBase, action, invariant, state

Position = tuple[int, int]
Piece = set[Position]

W = 4
H = 5


def pos_set() -> Annotated[Expr, set[Position]]:
    return AllTuples(Set(Val(0), ..., Val(W - 1)), Set(Val(0), ..., Val(H - 1)))


def piece(*cells: tuple[int, int]) -> Annotated[Expr, set[Position]]:
    return Set(*[Tuple(Val(x), Val(y)) for (x, y) in cells])


def klotski_init() -> Annotated[Expr, set[Piece]]:
    return Set(
        piece((0, 0), (0, 1)),
        piece((1, 0), (2, 0), (1, 1), (2, 1)),
        piece((3, 0), (3, 1)),
        piece((0, 2), (0, 3)),
        piece((1, 2), (2, 2)),
        piece((3, 2), (3, 3)),
        piece((1, 3)),
        piece((2, 3)),
        piece((0, 4)),
        piece((3, 4)),
    )


def klotski_goal() -> Annotated[Expr, set[Position]]:
    return piece((1, 3), (1, 4), (2, 3), (2, 4))


def dir_set() -> Annotated[Expr, set[Position]]:
    return Set(
        Tuple(Val(1), Val(0)),
        Tuple(Val(0), Val(1)),
        Tuple(Val(-1), Val(0)),
        Tuple(Val(0), Val(-1)),
    )


@state
class SlidingPuzzlesState(MachineStateBase):
    board: StateVar[set[Piece]]


def choose_one(
    candidates: Expr,
    predicate,
) -> Expr:
    matching = candidates.filter(predicate)
    return matching.choose(
        lambda x: matching.forall(lambda y: predicate(y).implies(x == y))
    )


@expr(pure=True)
def dir(
    p: Annotated[Expr, Position],
    empty_positions: Annotated[Expr, set[Position]],
) -> Annotated[Expr, set[Position]]:
    return dir_set().filter(
        lambda d: And(
            pos_set().contains(Tuple(p[0] + d[0], p[1] + d[1])),
            ~empty_positions.contains(Tuple(p[0] + d[0], p[1] + d[1])),
        )
    )


@expr
def move(
    s: SlidingPuzzlesState,
    p: Annotated[Expr, Position],
    d: Annotated[Expr, Position],
) -> Annotated[Expr, tuple[Piece, Piece]]:
    src = Tuple(p[0] + d[0], p[1] + d[1])
    pc = choose_one(s.board, lambda piece_: piece_.contains(src))
    moved = pc.map(lambda q: Tuple(q[0] - d[0], q[1] - d[1]))
    return Tuple(pc, moved)


@expr
def update(
    s: SlidingPuzzlesState,
    empty_position: Annotated[Expr, Position],
    empty_positions: Annotated[Expr, set[Position]],
) -> Annotated[Expr, set[set[Piece]]]:
    moved = dir(empty_position, empty_positions).map(
        lambda d: move(s, empty_position, d)
    )
    free = moved.filter(
        lambda pair: And(
            pair[1].intersect((s.board - Set(pair[0])).flattened).is_empty,
            pair[1].forall(lambda p: pos_set().contains(p)),
        )
    )
    return free.map(lambda pair: (s.board - Set(pair[0])) | Set(pair[1]))


@action(init=True)
def init(c: Context[SlidingPuzzlesState]):
    c.state.board = klotski_init()


@action
def step(c: Context[SlidingPuzzlesState]):
    s = c.state
    empty = pos_set() - s.board.flattened
    with c.one_of(empty, "e") as e:
        with c.one_of(update(s, e, empty), "next_board") as next_board:
            s.board = next_board


@invariant
def type_ok(s: SlidingPuzzlesState) -> Annotated[Expr, bool]:
    return s.board <= AllSubsets(pos_set())


@invariant
def klotski_goal_not_reached(s: SlidingPuzzlesState) -> Annotated[Expr, bool]:
    return ~s.board.contains(klotski_goal())


@coverage
def state_cov(s: SlidingPuzzlesState) -> Expr:
    return s.board
