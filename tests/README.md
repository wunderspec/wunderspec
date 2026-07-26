# Test Coverage and Provenance

The unit tests from the Wunderspec development repository are omitted from this
public distribution to keep the package focused. The full development
`make test` suite currently collects 2459 tests.

## Release Provenance

- Release tag: `v0.137.1`
- Source commit: `2629f8b78949bbe79e03506d42495dd6558d4686`
- Test log captured at: `2026-07-26T09:59:55Z`
- `make test` exit code: `0`

## Full Test Log From the Development Repository

```text
cd . && uv run pytest
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/runner/work/wunderspec-dev/wunderspec-dev/release-source
configfile: pyproject.toml
plugins: hypothesis-6.155.2, pytest_codeblocks-0.17.0, markdown-pytest-0.3.2, cov-6.3.0, subtests-0.15.0
collected 2459 items

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
..........................                                               [  5%]
tests/test_ast_properties.py ..................                          [  5%]
tests/test_ast_record.py ...................................             [  7%]
tests/test_ast_terms.py ................................................ [  9%]
....................................................                     [ 11%]
tests/test_ast_tuple.py .................................                [ 12%]
tests/test_cache.py .............................                        [ 13%]
tests/test_cli.py ...................................................... [ 15%]
........ssssss.........................................................  [ 18%]
tests/test_conditional.py ..............                                 [ 19%]
tests/test_direct_pc_examples.py .                                       [ 19%]
tests/test_enabled_eval.py ............                                  [ 19%]
tests/test_exec_context.py .................                             [ 20%]
tests/test_exec_context_complex.py ......                                [ 20%]
tests/test_expr_update.py ................                               [ 21%]
tests/test_flow.py ............................                          [ 22%]
tests/test_from_python.py .............................................  [ 24%]
tests/test_fuzzer.py ................................                    [ 25%]
tests/test_generator_exprs.py .........................................  [ 27%]
tests/test_interpreter_booleans.py ..................................... [ 28%]
...                                                                      [ 29%]
tests/test_interpreter_enums.py ...............                          [ 29%]
tests/test_interpreter_errors.py ....                                    [ 29%]
tests/test_interpreter_integers.py ..................................... [ 31%]
.................                                                        [ 32%]
tests/test_interpreter_let.py ..............                             [ 32%]
tests/test_interpreter_lists.py ........................................ [ 34%]
...................................................................      [ 37%]
tests/test_interpreter_map.py .......................................... [ 38%]
                                                                         [ 38%]
tests/test_interpreter_quantifiers.py .............................      [ 39%]
tests/test_interpreter_record.py .................................       [ 41%]
tests/test_interpreter_sampling.py ..................................... [ 42%]
..                                                                       [ 42%]
tests/test_interpreter_sets.py ......................................... [ 44%]
........................................................................ [ 47%]
............................................                             [ 49%]
tests/test_interpreter_state.py .............                            [ 49%]
tests/test_interpreter_to_python.py .................................... [ 51%]
.                                                                        [ 51%]
tests/test_interpreter_tuple.py .......................                  [ 52%]
tests/test_interpreter_unions.py ....................................    [ 53%]
tests/test_interpreter_value_sort.py ................................... [ 55%]
...........................................                              [ 56%]
tests/test_is_empty.py .................                                 [ 57%]
tests/test_lang_booleans.py ..................................           [ 58%]
tests/test_lang_expr_decorator.py ............                           [ 59%]
tests/test_lang_integers.py ............................................ [ 61%]
.                                                                        [ 61%]
tests/test_lang_lists.py ............................................... [ 63%]
.....................................                                    [ 64%]
tests/test_lang_literals.py ..............                               [ 65%]
tests/test_lang_maps.py ....................................             [ 66%]
tests/test_lang_record.py ................................               [ 67%]
tests/test_lang_sets.py ................................................ [ 69%]
........................................................................ [ 72%]
................................................................         [ 75%]
tests/test_lang_temporal.py ............................................ [ 77%]
.........                                                                [ 77%]
tests/test_lang_tuples.py ..........................                     [ 78%]
tests/test_lang_unions.py ......................................         [ 80%]
tests/test_linter.py ......................                              [ 81%]
tests/test_machine_edit.py ...........................................   [ 82%]
tests/test_model_checker.py ..............................               [ 84%]
tests/test_permutation.py ..................                             [ 84%]
tests/test_pretty_printing.py ....................................ss.... [ 86%]
..s                                                                      [ 86%]
tests/test_quint_convert.py ..........                                   [ 87%]
tests/test_quint_translation_manifest.py .....                           [ 87%]
tests/test_random_walk_replay.py .....                                   [ 87%]
tests/test_random_walk_seeds.py ....                                     [ 87%]
tests/test_record_decorator.py ............                              [ 88%]
tests/test_release_distribution.py .................                     [ 88%]
tests/test_schedule_enumerator.py ...................                    [ 89%]
tests/test_serialization.py ......                                       [ 89%]
tests/test_source_tracking.py .............                              [ 90%]
tests/test_state_view.py ....................                            [ 91%]
tests/test_sym_context.py ..................                             [ 91%]
tests/test_tla.py ...................................................... [ 94%]
........................................................................ [ 96%]
.............................................                            [ 98%]
tests/test_tlc_trace.py ....                                             [ 98%]
tests/test_trace_output.py .........................                     [100%]

====================== 2448 passed, 11 skipped in 30.42s =======================
```
