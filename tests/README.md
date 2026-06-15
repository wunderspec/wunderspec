# Test Coverage and Provenance

The unit tests from the Wunderspec development repository are omitted from this
public distribution to keep the package focused. The full development
`make test` suite currently collects 2350 tests.

## Release Provenance

- Release tag: `v0.129.1`
- Source commit: `604d6a23a444006bb67b14e0728df07711d3facb`
- Test log captured at: `2026-06-15T21:08:25Z`
- `make test` exit code: `0`

## Full Test Log From the Development Repository

```text
cd . && uv run pytest
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-8.4.2, pluggy-1.6.0
rootdir: /home/runner/work/wunderspec-dev/wunderspec-dev/release-source
configfile: pyproject.toml
plugins: pytest_codeblocks-0.17.0, subtests-0.15.0, hypothesis-6.155.2, cov-6.3.0, markdown-pytest-0.3.2
collected 2350 items

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
tests/test_action_execute.py ......                                      [  1%]
tests/test_api.py ...s..s............................................... [  3%]
..........                                                               [  3%]
tests/test_ast_properties.py ..................                          [  4%]
tests/test_ast_record.py ...................................             [  6%]
tests/test_ast_terms.py ................................................ [  8%]
....................................................                     [ 10%]
tests/test_ast_tuple.py .................................                [ 11%]
tests/test_cache.py .............................                        [ 12%]
tests/test_cli.py ...................................................... [ 15%]
......ssssss..............................................               [ 17%]
tests/test_conditional.py ..............                                 [ 18%]
tests/test_direct_pc_examples.py .                                       [ 18%]
tests/test_enabled_eval.py ............                                  [ 18%]
tests/test_exec_context.py .................                             [ 19%]
tests/test_exec_context_complex.py ......                                [ 19%]
tests/test_expr_update.py ................                               [ 20%]
tests/test_flow.py ............................                          [ 21%]
tests/test_from_python.py .............................................  [ 23%]
tests/test_fuzzer.py ................................                    [ 24%]
tests/test_generator_exprs.py .............................              [ 26%]
tests/test_interpreter_booleans.py ..................................... [ 27%]
.                                                                        [ 27%]
tests/test_interpreter_enums.py ...............                          [ 28%]
tests/test_interpreter_errors.py ....                                    [ 28%]
tests/test_interpreter_integers.py ..................................... [ 30%]
.....                                                                    [ 30%]
tests/test_interpreter_let.py ..............                             [ 31%]
tests/test_interpreter_lists.py ........................................ [ 32%]
...................................................................      [ 35%]
tests/test_interpreter_map.py .......................................... [ 37%]
                                                                         [ 37%]
tests/test_interpreter_quantifiers.py .............................      [ 38%]
tests/test_interpreter_record.py .................................       [ 40%]
tests/test_interpreter_sampling.py ..................................... [ 41%]
..                                                                       [ 41%]
tests/test_interpreter_sets.py ......................................... [ 43%]
........................................................................ [ 46%]
............................................                             [ 48%]
tests/test_interpreter_state.py .............                            [ 48%]
tests/test_interpreter_to_python.py .................................... [ 50%]
.                                                                        [ 50%]
tests/test_interpreter_tuple.py .......................                  [ 51%]
tests/test_interpreter_unions.py ...................................     [ 52%]
tests/test_interpreter_value_sort.py ................................... [ 54%]
...........................................                              [ 56%]
tests/test_is_empty.py .................                                 [ 56%]
tests/test_lang_booleans.py ..................................           [ 58%]
tests/test_lang_expr_decorator.py ............                           [ 58%]
tests/test_lang_integers.py ................................             [ 60%]
tests/test_lang_lists.py ............................................... [ 62%]
.....................................                                    [ 63%]
tests/test_lang_literals.py ..............                               [ 64%]
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
tests/test_quint_convert.py ........                                     [ 87%]
tests/test_quint_translation_manifest.py .....                           [ 87%]
tests/test_random_walk_replay.py .....                                   [ 87%]
tests/test_random_walk_seeds.py ....                                     [ 87%]
tests/test_record_decorator.py ............                              [ 88%]
tests/test_release_distribution.py .......                               [ 88%]
tests/test_schedule_enumerator.py ...................                    [ 89%]
tests/test_serialization.py ......                                       [ 89%]
tests/test_source_tracking.py .............                              [ 90%]
tests/test_state_view.py ....................                            [ 91%]
tests/test_sym_context.py ..................                             [ 91%]
tests/test_tla.py ...................................................... [ 94%]
........................................................................ [ 97%]
....................................                                     [ 98%]
tests/test_tlc_trace.py ....                                             [ 98%]
tests/test_trace_output.py .........................                     [100%]

====================== 2339 passed, 11 skipped in 25.79s =======================
```
