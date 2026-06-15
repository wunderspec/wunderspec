#!/usr/bin/env python
# Unit tests for `simple_ponzi_machine`.

import pytest
from simple_ponzi_machine import *

from examples.simple_ponzi_machine import PonziMachineState
from wunderspec.exec import AssumptionViolated, ExecContext
from wunderspec.interpreter_value import to_python


class TestPonziStateMachine:
    """Simple tests for the Ponzi state machine."""

    def test_happy_path(self):
        s0 = PonziMachineState()
        c = ExecContext(s0, scheduler=[])
        c.step(init)
        c.step(on_receive, "bob", 100)
        c.step(on_receive, "eve", 110)
        c.step(on_receive, "alice", 121)
        # In ExecContext the state vars hold concrete IValues at runtime, though
        # they are statically typed as symbolic Expr (StateVar).
        ps = to_python(c.state.ponzi_state)  # type: ignore[arg-type]
        es = to_python(c.state.evm_state)  # type: ignore[arg-type]
        assert ps.currentInvestor == "alice"
        assert ps.currentInvestment == 121
        assert es.balances["alice"] == 9979
        assert es.balances["eve"] == 10011

    def test_unhappy_path(self):
        s0 = PonziMachineState()
        c = ExecContext(s0, scheduler=[])
        c.step(init)
        c.step(on_receive, "bob", 100)
        with pytest.raises(AssumptionViolated):
            # This should raise AssumptionViolated because 105 < 110 (10% increase)
            c.step(on_receive, "eve", 105)
