"""What an author reads when a spec is refused.

`validate` hands these lines back to the AI that wrote the spec, so each one must
name the key and say what is wrong with it — "is not valid under any of the given
schemas" gives a small model nothing to fix.
"""

from __future__ import annotations

from chart_view.spec import parse_spec, spec_errors

BASE = "view: chart\nsource: data/a.csv\n"
ENC = "encoding:\n  x: {field: a, type: quantitative}\n  y: {field: b, type: quantitative}\n"


def _errors(text: str) -> list[str]:
    return spec_errors(parse_spec(text))


def test_an_unsupported_mark_lists_the_marks_there_are():
    [line] = _errors(BASE + "mark: geoshape\n" + ENC)
    assert line.startswith("mark: ")
    assert "'geoshape'" in line
    assert "scatter" in line and "errorbar" in line


def test_an_unknown_mark_property_is_named():
    [line] = _errors(BASE + "mark: {type: line, strokeDash: [4, 2]}\n" + ENC)
    assert "strokeDash" in line


def test_a_field_without_a_type_says_type_is_required():
    [line] = _errors(
        BASE + "mark: line\nencoding:\n  x: {field: a}\n  y: {field: b, type: nominal}\n"
    )
    assert line.startswith("encoding.x: ")
    assert "'type' is a required property" in line


def test_an_unsupported_aggregate_names_the_op():
    [line] = _errors(
        BASE + "mark: bar\nencoding:\n  x: {field: a, type: nominal}\n"
        "  y: {field: b, type: quantitative, aggregate: median}\n"
    )
    assert line.startswith("encoding.y.aggregate: ")
    assert "'median'" in line


def test_a_missing_diff_side_is_named():
    [line] = _errors(
        BASE + "mark: line\n" + ENC + "transform:\n  - diff: {by: phase, of: after}\n"
        "    aggregate: [{op: mean, field: t, as: d}]\n"
    )
    assert "'minus' is a required property" in line


def test_mark_and_layer_together_says_to_pick_one():
    [line] = _errors(
        BASE + "mark: line\n" + ENC + "layer:\n  - mark: rule\n    encoding:\n      y: {datum: 1}\n"
    )
    assert "either mark + encoding, or layer" in line


def test_a_source_that_is_not_a_table_file_says_which_files_are():
    [line] = _errors("view: chart\nsource: data/a.xlsx\nmark: line\n" + ENC)
    assert line.startswith("source: ")
    assert "not of type 'object'" not in line
    assert ".csv" in line and ".parquet" in line


def test_a_predicate_without_a_test_lists_the_tests():
    [line] = _errors(BASE + "mark: line\n" + ENC + "transform:\n  - filter: {field: a}\n")
    assert line.startswith("transform[0].filter: ")
    assert "oneOf" in line and "range" in line and "valid" in line


def test_nothing_to_draw_says_what_is_missing():
    [line] = _errors(BASE + "title: t\n")
    assert "either mark + encoding, or layer" in line


OFF_RULE = "a datum is drawn only as a rule's x or y — drop it and name a field here"


def test_a_datum_where_none_is_drawn_says_why():
    # Review round 8: the line said only "'field' is a required property", and
    # a model tried another datum. The renderer's spec.test.ts reads the same.
    area = (
        "mark: area\nencoding:\n  x: {field: a, type: quantitative}\n"
        "  y: {field: b, type: quantitative}\n  y2: {datum: 0}\n"
    )
    assert _errors(BASE + area) == [f"encoding.y2: {OFF_RULE}"]


def test_a_datum_on_a_mark_that_also_needs_the_field_says_it_once():
    grid = (
        "mark: grid\nencoding:\n  x: {datum: 1}\n  y: {field: b, type: ordinal}\n"
        "  color: {field: c, type: quantitative}\n"
    )
    lines = _errors(BASE + grid)
    assert [line for line in lines if line.startswith("encoding.x:")] == [f"encoding.x: {OFF_RULE}"]


def test_a_line_with_an_x_datum_gets_one_line_that_says_why():
    # Round 8 regression lens: markChannels and datumChannels each refused it,
    # so the model read the same key refused twice, with no reason either time.
    text = BASE + "mark: line\nencoding:\n  x: {datum: 1}\n  y: {field: b, type: quantitative}\n"
    assert _errors(text) == [f"encoding.x: {OFF_RULE}"]


def test_a_colour_datum_is_refused_too():
    # SKILL.md says a datum is a rule's x or y only; the schema took one on
    # color / size / tooltip, which the renderer never reads.
    text = BASE + "mark: scatter\n" + ENC + "  color: {datum: red}\n"
    assert _errors(text) == [f"encoding.color: {OFF_RULE}"]


def test_a_channel_with_no_field_and_no_datum_is_not_told_about_a_datum():
    # Review round 9: `x: {aggregate: count}` (a Vega-Lite habit) was told "a
    # datum is drawn only as a rule's x or y" — it holds no datum.
    text = (
        BASE
        + "mark: bar\nencoding:\n  x: {aggregate: count}\n  y: {field: b, type: quantitative}\n"
    )
    lines = _errors(text)
    assert lines and not any("datum is drawn" in line for line in lines), lines


def test_a_channel_with_a_field_and_a_datum_is_told_to_drop_the_datum():
    # Round 10: the line said "name a field here" to a channel that named one.
    text = BASE + "mark: scatter\n" + ENC + "  color: {datum: 1, field: a, type: nominal}\n"
    assert f"encoding.color: {OFF_RULE}" in _errors(text)
    assert "drop it" in OFF_RULE


def test_a_tooltip_item_that_is_no_channel_is_not_told_about_a_datum():
    # The datum rule is scoped to objects: a string item is refused for what
    # it is, not as a datum it does not hold.
    text = BASE + "mark: scatter\n" + ENC + "  tooltip: [a]\n"
    lines = _errors(text)
    assert lines and not any("datum is drawn" in line for line in lines), lines


STACK_BY_VALUE = (
    "a stack cannot be coloured by value (quantitative): which rows form one segment of it "
    "is not defined — colour it by a category (nominal or ordinal), or drop stack"
)
STACK = (
    "mark: {type: bar, stack: true}\nencoding:\n  x: {field: a, type: nominal}\n"
    "  y: {field: b, type: quantitative}\n  color: {field: c, type: quantitative}\n"
)


def test_a_stack_coloured_by_value_says_why():
    # #847/#848 PR 5 P40 row 18: a stack is summed per slot and colour, and a
    # colour by value makes no segments. The renderer's spec.test.ts reads the same.
    assert _errors(BASE + STACK) == [f"encoding.color: {STACK_BY_VALUE}"]


def test_a_layer_stack_coloured_by_value_says_why():
    layered = "layer:\n  - mark: {type: area, stack: true}\n    encoding:\n" + "".join(
        f"      {line}\n" for line in STACK.splitlines()[2:]
    )
    assert _errors(BASE + layered) == [f"layer[0].encoding.color: {STACK_BY_VALUE}"]


def test_a_stack_coloured_by_a_category_or_no_stack_is_not_refused():
    enc = "encoding:\n  x: {field: a, type: nominal}\n  y: {field: b, type: quantitative}\n"
    for mark, colour in [
        ("{type: bar, stack: true}", "nominal"),
        ("{type: area, stack: true}", "ordinal"),
        ("{type: area, stack: true}", "temporal"),
        ("{type: bar, stack: false}", "quantitative"),
        ("{type: bar}", "quantitative"),
        ("{type: line, stack: true}", "quantitative"),
        ("bar", "quantitative"),
    ]:
        text = BASE + f"mark: {mark}\n" + enc + f"  color: {{field: c, type: {colour}}}\n"
        assert _errors(text) == [], (mark, colour)


# #847/#848 PR 5 P41 row 25: a stack sums its value channel; a time summed
# nanoseconds and a category summed text. The renderer's spec.test.ts reads
# the same.
STACK_VALUE = (
    "a stack sums its rows, so its value channel must be quantitative — a time or "
    "a category has no sum: make it quantitative, or drop stack"
)


def _stack(mark: str, x: str, y: str) -> str:
    return (
        BASE + f"mark: {mark}\nencoding:\n  x: {{field: a, type: {x}}}\n"
        f"  y: {{field: b, type: {y}}}\n  color: {{field: c, type: nominal}}\n"
    )


def test_a_stack_whose_value_is_not_a_number_says_why():
    # upright: the value is y; horizontal (y a category): the value is x
    for mark, x, y, channel in [
        ("{type: bar, stack: true}", "nominal", "temporal", "y"),
        ("{type: area, stack: true}", "ordinal", "nominal", "x"),
        ("{type: area, stack: true}", "temporal", "ordinal", "x"),
        ("{type: bar, stack: true}", "temporal", "ordinal", "x"),
        ("{type: bar, stack: true}", "nominal", "nominal", "x"),
    ]:
        want = [f"encoding.{channel}: {STACK_VALUE}"]
        assert _errors(_stack(mark, x, y)) == want, (mark, x, y)


def test_a_layer_stack_whose_value_is_a_time_says_why():
    layered = BASE + (
        "layer:\n  - mark: {type: bar, stack: true}\n    encoding:\n"
        "      x: {field: a, type: nominal}\n      y: {field: b, type: temporal}\n"
    )
    assert _errors(layered) == [f"layer[0].encoding.y: {STACK_VALUE}"]


def _two_ops(y: str, tip: str) -> str:
    return (
        BASE + "mark: bar\nencoding:\n  x: {field: item, type: nominal}\n"
        f"  y: {{field: value, type: quantitative{y}}}\n"
        f"  tooltip:\n    - {{field: item, type: nominal}}\n"
        f"    - {{field: value, type: quantitative{tip}}}\n"
    )


def test_one_field_aggregated_two_ways_is_refused():
    # #847/#848 PR 5 P41 row 26: a layer aggregates a field once (`_measures`:
    # the first op wins), and a tooltip labelled max showed the mean. The
    # renderer's spec.test.ts reads the same.
    assert _errors(_two_ops(", aggregate: mean", ", aggregate: max")) == [
        "encoding.tooltip[1]: 'value' is aggregated as mean on y — a field has one "
        "aggregate in a layer, so max here would show the mean: use mean here too, "
        "or compute both in a transform aggregate, each under its own name (as:)"
    ]


def test_one_field_aggregated_two_ways_by_a_single_tooltip_is_refused():
    text = BASE + (
        "mark: bar\nencoding:\n  x: {field: item, type: nominal}\n"
        "  y: {field: value, type: quantitative, aggregate: mean}\n"
        "  tooltip: {field: value, type: quantitative, aggregate: max}\n"
    )
    [line] = _errors(text)
    assert line.startswith("encoding.tooltip: 'value' is aggregated as mean on y — ")


def test_one_field_aggregated_two_ways_in_a_layer_is_refused():
    layered = BASE + (
        "layer:\n  - mark: line\n    encoding:\n      x: {field: item, type: nominal}\n"
        "      y: {field: value, type: quantitative, aggregate: sum}\n"
        "      size: {field: value, type: quantitative, aggregate: count}\n"
    )
    [line] = _errors(layered)
    assert line.startswith("layer[0].encoding.size: 'value' is aggregated as sum on y — ")


def test_one_field_aggregated_one_way_is_not_refused():
    for y, tip in [
        (", aggregate: mean", ", aggregate: mean"),
        (", aggregate: mean", ""),
        ("", ", aggregate: max"),
        ("", ""),
    ]:
        assert _errors(_two_ops(y, tip)) == [], (y, tip)


def _stack_tip(y: str, tip: str, horizontal: bool = False) -> str:
    slot, value = ("y", "x") if horizontal else ("x", "y")
    return BASE + (
        f"mark: {{type: bar, stack: true}}\nencoding:\n  {slot}: {{field: item, type: nominal}}\n"
        f"  {value}: {{field: value, type: quantitative{y}}}\n"
        f"  tooltip: [{{field: value, type: quantitative{tip}}}]\n"
    )


def test_a_stacks_own_sum_is_its_values_op():
    # #847/#848 PR 5 P42 row 31: a stack sums its value channel when it names
    # no aggregate (P40 row 18), so a tooltip mean of that field showed sums
    # under a "mean" label. The renderer's spec.test.ts reads the same.
    assert _errors(_stack_tip("", ", aggregate: mean")) == [
        "encoding.tooltip[0]: 'value' is aggregated as sum on y — a field has one "
        "aggregate in a layer, so mean here would show the sum: use sum here too, "
        "or compute both in a transform aggregate, each under its own name (as:)"
    ]
    [line] = _errors(_stack_tip("", ", aggregate: max", horizontal=True))
    assert line.startswith("encoding.tooltip[0]: 'value' is aggregated as sum on x — ")
    [line] = _errors(_stack_tip(", aggregate: mean", ", aggregate: sum"))
    assert line.startswith("encoding.tooltip[0]: 'value' is aggregated as mean on y — ")


def test_a_stacks_tooltip_with_its_own_op_is_not_refused():
    for y, tip in [("", ", aggregate: sum"), ("", ""), (", aggregate: mean", ", aggregate: mean")]:
        assert _errors(_stack_tip(y, tip)) == [], (y, tip)
        assert _errors(_stack_tip(y, tip, horizontal=True)) == [], (y, tip)


def test_a_stack_whose_value_is_a_number_or_no_stack_is_not_refused():
    for mark, x, y in [
        ("{type: bar, stack: true}", "nominal", "quantitative"),
        ("{type: bar, stack: true}", "temporal", "quantitative"),
        ("{type: bar, stack: true}", "quantitative", "nominal"),
        ("{type: area, stack: true}", "quantitative", "ordinal"),
        ("{type: bar, stack: false}", "nominal", "temporal"),
        ("{type: line, stack: true}", "nominal", "temporal"),
        ("bar", "quantitative", "ordinal"),
    ]:
        assert _errors(_stack(mark, x, y)) == [], (mark, x, y)


# #847/#848 PR 5 P41 row 21 [user, 2026-09-26]: a stack links by its slot and
# colour only. A segment is the sum of its rows, so it has no single value of
# any other field: a brush over one wrote `{}` for a row-id key (clearing
# every linked view), and a highlight on another field lit nothing. The
# renderer's spec.test.ts reads the same.
STACKED_BAR = (
    "mark: {type: bar, stack: true}\nencoding:\n  x: {field: item, type: nominal}\n"
    "  y: {field: value, type: quantitative}\n  color: {field: group, type: nominal}\n"
    "  tooltip: {field: region, type: nominal}\n"
)
LINKS = (
    "a stack links by its slot and colour only ('item', 'group') — a segment is the sum of its rows"
)


def test_a_stack_keyed_by_another_field_is_refused():
    assert _errors(BASE + "keys: [item, id, group, region]\n" + STACKED_BAR) == [
        f"keys: {LINKS}, so it has no single 'id', 'region': key the view by its slot "
        "and colour, or drop stack so single rows link"
    ]


def test_a_stack_highlighting_another_field_is_refused():
    where = BASE + "highlight: {where: \"region == 'n' and value > 3\"}\n" + STACKED_BAR
    assert _errors(where) == [
        f"highlight.where: {LINKS}, so it has no single 'region': test its slot and "
        "colour, or its value 'value' (each segment's sum), or drop stack so single rows light"
    ]
    values = BASE + "highlight: {values: {item: [p], id: [r1]}}\n" + STACKED_BAR
    [line] = _errors(values)
    assert line.startswith(f"highlight.values: {LINKS}, so it has no single 'id': ")


def test_a_stacks_refusal_names_the_op_its_segments_are():
    # #847/#848 PR 5 P42 row 33: "the sum of its rows" was false for a stack
    # whose value names its own aggregate. The renderer's spec.test.ts reads
    # the same.
    mean = STACKED_BAR.replace("type: quantitative}", "type: quantitative, aggregate: mean}")
    links = LINKS.replace("the sum of its rows", "the mean of its rows")
    assert _errors(BASE + "keys: [id]\n" + mean) == [
        f"keys: {links}, so it has no single 'id': key the view by its slot "
        "and colour, or drop stack so single rows link"
    ]
    assert _errors(BASE + "highlight: {where: \"region == 'n'\"}\n" + mean) == [
        f"highlight.where: {links}, so it has no single 'region': test its slot and "
        "colour, or its value 'value' (each segment's mean), or drop stack so single rows light"
    ]


# #847/#848 PR 5 P42 row 29 [user, 2026-09-26]: in a layered chart the stack
# rule limits the stack layer only. A layer that is not stacked links by any
# field its rows have, so `keys:` / `highlight:` are refused here only when
# no layer is unstacked; whether one really has the field is `validate`'s
# (it reads the data). The renderer's spec.test.ts reads the same.
STACK_AND_POINTS = (
    "layer:\n  - mark: {type: bar, stack: true}\n    encoding:\n"
    "      x: {field: group, type: nominal}\n      y: {field: value, type: quantitative}\n"
    "  - mark: scatter\n    encoding:\n      x: {field: group, type: nominal}\n"
    "      y: {field: value, type: quantitative}\n      tooltip: {field: item, type: nominal}\n"
)


def test_a_stack_beside_an_unstacked_layer_limits_only_itself():
    for top in [
        "keys: [item]\n",
        "keys: [group, item]\n",
        "highlight: {where: \"item == 'x'\"}\n",
        "highlight: {values: {item: [x]}}\n",
    ]:
        assert _errors(BASE + top + STACK_AND_POINTS) == [], top


def test_a_chart_of_stacks_only_names_each_stack():
    stacks = STACK_AND_POINTS.replace("mark: scatter", "mark: {type: area, stack: true}")
    assert _errors(BASE + "keys: [item]\n" + stacks) == [
        f"keys: a stack (layer[{i}]) links by its slot and colour only ('group') — a segment "
        "is the sum of its rows, so it has no single 'item': key the view by its slot and "
        "colour, or drop stack so single rows link"
        for i in (0, 1)
    ]


def test_a_field_another_stack_links_by_is_not_refused():
    # layer[1] is a stack by item: it writes and lights by item, layer[0] not
    stacks = STACK_AND_POINTS.replace("mark: scatter", "mark: {type: area, stack: true}")
    stacks = stacks.replace(
        "      x: {field: group, type: nominal}\n      y: {field: value, type: quantitative}\n"
        "      tooltip",
        "      x: {field: group, type: nominal}\n      y: {field: value, type: quantitative}\n"
        "      color: {field: item, type: nominal}\n      tooltip",
    )
    assert "color: {field: item" in stacks
    for top in ["keys: [item]\n", "highlight: {where: \"item == 'x'\"}\n"]:
        assert _errors(BASE + top + stacks) == [], top
    [line] = _errors(BASE + "keys: [region]\n" + stacks)[:1]
    assert line.startswith("keys: a stack (layer[0]) ") and "no single 'region'" in line
    # a highlight may test another stack's value (each of its segments' sum)
    sized = stacks.replace(
        "y: {field: value, type: quantitative}\n      color",
        "y: {field: size, type: quantitative}\n      color",
    )
    assert "field: size" in sized
    assert _errors(BASE + "highlight: {where: 'size > 1'}\n" + sized) == []
    [line] = _errors(BASE + "keys: [size]\n" + sized)[:1]
    assert "no single 'size'" in line


def test_a_horizontal_stack_links_by_its_y():
    horizontal = (
        "mark: {type: bar, stack: true}\nencoding:\n  x: {field: value, type: quantitative}\n"
        "  y: {field: item, type: ordinal}\n"
    )
    [line] = _errors(BASE + "keys: [group]\n" + horizontal)
    assert "only ('item')" in line and "no single 'group'" in line
    assert _errors(BASE + "keys: [item]\n" + horizontal) == []


def test_a_stack_linked_by_its_slot_and_colour_or_lit_by_its_sum_is_not_refused():
    for top in [
        "keys: [item, group]\n",
        "keys: [group]\n",
        "highlight: {where: \"value > 10 and group == 'a'\"}\n",
        "highlight: {where: \"`item` == 'p' and index > 0\"}\n",
        # P42 row 30: a keyword argument and pandas' globals are no fields
        "highlight: {where: \"group.str.contains('A', case=False) and value < inf\"}\n",
        "highlight: {values: {item: [p], value: [13]}}\n",
    ]:
        assert _errors(BASE + top + STACKED_BAR) == [], top


def test_single_rows_link_by_any_field():
    for mark in ["{type: bar, stack: false}", "bar", "{type: line, stack: true}"]:
        text = BASE + "keys: [id]\nhighlight: {where: \"region == 'n'\"}\n"
        text += STACKED_BAR.replace("{type: bar, stack: true}", mark)
        assert _errors(text) == [], mark
