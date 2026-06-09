import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { blankGroup } from "../../forms/editorModel";
import { GroupEditor } from "../FormEditor";

afterEach(cleanup);

// Regression: the response scale used to be a single comma-separated <input> that round-tripped
// through split/trim/filter on every keystroke — so a trailing comma was erased (couldn't add a
// 4th option) and an option that briefly emptied while editing vanished. It now reuses the
// per-option OptionsEditor (add/remove rows), the same control used for select / circle_one options.
describe("GroupEditor response scale", () => {
  it("edits the scale via per-option rows, not a single comma field", () => {
    const onChange = vi.fn();
    const { getAllByPlaceholderText, queryByDisplayValue } = render(
      <GroupEditor group={blankGroup("g1")} onChange={onChange} />,
    );
    // One input per scale option (the three defaults)...
    expect(getAllByPlaceholderText("option value")).toHaveLength(3);
    // ...and NOT the old joined comma field.
    expect(queryByDisplayValue("OK, NOT OK, N/A")).toBeNull();
  });

  it("'+ Add option' appends a 4th option (previously impossible)", () => {
    const onChange = vi.fn();
    const { getByText } = render(<GroupEditor group={blankGroup("g1")} onChange={onChange} />);
    fireEvent.click(getByText("+ Add option"));
    expect(onChange.mock.calls[0][0].scale).toEqual(["OK", "NOT OK", "N/A", ""]);
  });
});
