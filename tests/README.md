# Test Coverage and Provenance

The unit tests from the Wunderspec development repository are omitted from this
public distribution to keep the package focused. The full development
`make test` suite currently collects 2541 tests.

## Release Provenance

- Release tag: `v0.137.3`
- Source commit: `be86a4dd5da2668b37f5287a80ab13ef025b645c`
- Test log captured at: `2026-10-05T12:42:59Z`
- `make test` exit code: `0`

## Full Test Log From the Development Repository

```text
cd . && uv run pytest
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/runner/work/wunderspec-dev/wunderspec-dev/release-source
configfile: pyproject.toml
plugins: subtests-0.15.0, pytest_codeblocks-0.17.0, markdown-pytest-0.3.2, hypothesis-6.155.2, cov-6.3.0
collected 2541 items

docs/user-references/booleans.md .                                       [  0%]
docs/user-references/comprehensions.md .                                 [  0%]
docs/user-references/decorators.md .                                     [  0%]
docs/user-references/enums.md .                                          [  0%]
docs/user-references/flow.md .                                           [  0%]
docs/user-references/integers.md .                                       [  0%]
docs/user-references/lists.md .                                          [  0%]
docs/user-references/maps.md .                                           [  0%]
docs/user-references/records.md .                                        [  0%]
docs/user-references/sets.md .                                           [  0%]
docs/user-references/state-machine.md .                                  [  0%]
docs/user-references/strings.md .                                        [  0%]
docs/user-references/temporal.md .                                       [  0%]
docs/user-references/tuples.md .                                         [  0%]
docs/user-references/unions.md .                                         [  0%]
examples/test_simple_ponzi_machine.py ..                                 [  0%]
tests/test_action_coercion.py ..                                         [  0%]
tests/test_action_execute.py ..........                                  [  1%]
tests/test_action_profile.py ...............                             [  1%]
tests/test_api.py .......s..s........................................... [  3%]
..........................                                               [  4%]
tests/test_ast_properties.py ..................                          [  5%]
tests/test_ast_record.py ...................................             [  6%]
tests/test_ast_terms.py ................................................ [  8%]
....................................................                     [ 10%]
tests/test_ast_tuple.py .................................                [ 12%]
tests/test_cache.py .............................                        [ 13%]
tests/test_cli.py ...................................................... [ 15%]
........sssssss......................................................... [ 18%]
                                                                         [ 18%]
tests/test_conditional.py ..............                                 [ 18%]
tests/test_direct_pc_examples.py .                                       [ 18%]
tests/test_enabled_eval.py ............                                  [ 19%]
tests/test_exec_context.py .................                             [ 20%]
tests/test_exec_context_complex.py ......                                [ 20%]
tests/test_expr_maintenance.py .................                         [ 20%]
tests/test_expr_update.py ................                               [ 21%]
tests/test_flow.py ............................                          [ 22%]
tests/test_from_python.py .............................................  [ 24%]
tests/test_fuzzer.py ................................                    [ 25%]
tests/test_generator_exprs.py .........................................  [ 27%]
tests/test_interpreter_booleans.py ..................................... [ 28%]
...                                                                      [ 28%]
tests/test_interpreter_enums.py ...............                          [ 29%]
tests/test_interpreter_errors.py ....                                    [ 29%]
tests/test_interpreter_integers.py ..................................... [ 31%]
.................                                                        [ 31%]
tests/test_interpreter_let.py ..............                             [ 32%]
tests/test_interpreter_lists.py ........................................ [ 33%]
...................................................................      [ 36%]
tests/test_interpreter_map.py .......................................... [ 38%]
                                                                         [ 38%]
tests/test_interpreter_quantifiers.py .............................      [ 39%]
tests/test_interpreter_record.py .................................       [ 40%]
tests/test_interpreter_sampling.py ..................................... [ 42%]
..                                                                       [ 42%]
tests/test_interpreter_sets.py ......................................... [ 43%]
........................................................................ [ 46%]
............................................                             [ 48%]
tests/test_interpreter_state.py .............                            [ 48%]
tests/test_interpreter_to_python.py .................................... [ 50%]
.                                                                        [ 50%]
tests/test_interpreter_tuple.py .......................                  [ 51%]
tests/test_interpreter_unions.py ....................................    [ 52%]
tests/test_interpreter_value_sort.py ................................... [ 53%]
...........................................                              [ 55%]
tests/test_is_empty.py .................                                 [ 56%]
tests/test_lang_booleans.py ..................................           [ 57%]
tests/test_lang_expr_decorator.py ............                           [ 58%]
tests/test_lang_integers.py ............................................ [ 59%]
.                                                                        [ 59%]
tests/test_lang_lists.py ............................................... [ 61%]
.....................................                                    [ 63%]
tests/test_lang_literals.py ..............                               [ 63%]
tests/test_lang_maps.py ....................................             [ 65%]
tests/test_lang_record.py ................................               [ 66%]
tests/test_lang_sets.py ................................................ [ 68%]
........................................................................ [ 71%]
................................................................         [ 73%]
tests/test_lang_temporal.py ............................................ [ 75%]
.........                                                                [ 75%]
tests/test_lang_tuples.py ..........................                     [ 76%]
tests/test_lang_unions.py ......................................         [ 78%]
tests/test_linter.py ......................                              [ 79%]
tests/test_machine_edit.py ...........................................   [ 80%]
tests/test_model_checker.py ..............................               [ 82%]
tests/test_permutation.py ..................                             [ 82%]
tests/test_pretty_printing.py ....................................ss.... [ 84%]
..s                                                                      [ 84%]
tests/test_quint_convert.py ...............                              [ 85%]
tests/test_quint_translation_manifest.py .....                           [ 85%]
tests/test_random_walk_replay.py .....                                   [ 85%]
tests/test_random_walk_seeds.py ....                                     [ 85%]
tests/test_record_decorator.py ............                              [ 86%]
tests/test_release_distribution.py .................                     [ 86%]
tests/test_schedule_enumerator.py ...................                    [ 87%]
tests/test_serialization.py ............................................ [ 89%]
.....................                                                    [ 90%]
tests/test_source_tracking.py .............                              [ 90%]
tests/test_state_view.py ....................                            [ 91%]
tests/test_sym_context.py ..................                             [ 92%]
tests/test_tla.py ...................................................... [ 94%]
........................................................................ [ 97%]
.............................................                            [ 98%]
tests/test_tlc_trace.py ....                                             [ 99%]
tests/test_trace_output.py .........................                     [100%]

=============================== warnings summary ===============================
tests/test_serialization.py::TestRoundTrip::test_lit_int
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:42: DeprecationWarning: save_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0).
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRoundTrip::test_lit_int
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:42: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRoundTrip::test_lit_bool
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:47: DeprecationWarning: save_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0).
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRoundTrip::test_lit_bool
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:47: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRoundTrip::test_var
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:52: DeprecationWarning: save_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0).
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRoundTrip::test_var
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:52: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRoundTrip::test_nested
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:57: DeprecationWarning: save_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0).
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRoundTrip::test_nested
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:57: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestRestrictedUnpickler::test_rejects_os_module
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:68: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    load_ast(payload)

tests/test_serialization.py::TestRestrictedUnpickler::test_rejects_non_node
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:74: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    load_ast(payload)

tests/test_serialization.py::TestSecurityRegression::test_eval_reduce_has_no_side_effects[eval-3]
tests/test_serialization.py::TestSecurityRegression::test_eval_reduce_has_no_side_effects[eval-4]
tests/test_serialization.py::TestSecurityRegression::test_eval_reduce_has_no_side_effects[eval-5]
tests/test_serialization.py::TestSecurityRegression::test_eval_reduce_has_no_side_effects[os-via-import-3]
tests/test_serialization.py::TestSecurityRegression::test_eval_reduce_has_no_side_effects[os-via-import-4]
tests/test_serialization.py::TestSecurityRegression::test_eval_reduce_has_no_side_effects[os-via-import-5]
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:103: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    load_ast(payload)

tests/test_serialization.py::TestSecurityRegression::test_protocol_two_explicit_builtins_eval
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:123: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    load_ast(payload)

tests/test_serialization.py::TestSecurityRegression::test_rejects_stdlib_reducers[namedtuple]
tests/test_serialization.py::TestSecurityRegression::test_rejects_stdlib_reducers[defaultdict]
tests/test_serialization.py::TestSecurityRegression::test_rejects_stdlib_reducers[enum-factory]
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:139: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    load_ast(payload)

tests/test_serialization.py::TestSecurityRegression::test_rejects_non_ast_globals_in_ast_modules[wunderspec.ast.ast-Enum]
tests/test_serialization.py::TestSecurityRegression::test_rejects_non_ast_globals_in_ast_modules[wunderspec.ast.sorts-sort_of]
tests/test_serialization.py::TestSecurityRegression::test_rejects_non_ast_globals_in_ast_modules[wunderspec.ast.sorts-get_origin]
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:152: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    load_ast(payload)

tests/test_serialization.py::TestSecurityRegression::test_cached_extension_cannot_bypass_allowlist[EXT1]
tests/test_serialization.py::TestSecurityRegression::test_cached_extension_cannot_bypass_allowlist[EXT2]
tests/test_serialization.py::TestSecurityRegression::test_cached_extension_cannot_bypass_allowlist[EXT4]
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:180: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    load_ast(payload)

tests/test_serialization.py: 40 warnings
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:207: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    assert load_ast(pickle.dumps(node, protocol=protocol)) == node

tests/test_serialization.py::TestCompatibility::test_source_span_roundtrip
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:212: DeprecationWarning: save_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0).
    restored = load_ast(save_ast(original))

tests/test_serialization.py::TestCompatibility::test_source_span_roundtrip
  /home/runner/work/wunderspec-dev/wunderspec-dev/release-source/tests/test_serialization.py:212: DeprecationWarning: load_ast is deprecated; migrate to wunderspec.transfer (available in wunderspec 0.138.0). Load only files you produced yourself.
    restored = load_ast(save_ast(original))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
================ 2529 passed, 12 skipped, 68 warnings in 30.59s ================
```
