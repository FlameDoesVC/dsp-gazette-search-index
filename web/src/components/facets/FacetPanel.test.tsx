import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FacetPanel } from "./FacetPanel";

const facets = [
  { key: "brand", label: "Brand", label_dv: "ބްރޭންޑް", widget: "checkbox",
    unit: "", values: [{ value: "Apple", label: "Apple", count: 12 },
                       { value: "Nokia", label: "Nokia", count: 3 }],
    min: null, max: null, histogram: [], count_true: null },
  { key: "price", label: "Price", label_dv: "އަގު", widget: "range", unit: "MVR",
    values: [], min: 100, max: 9500,
    histogram: [{ from: 100, to: 1040, count: 4 },
                { from: 1040, to: 1980, count: 9 }],
    count_true: null },
  { key: "has_images", label: "Has photos", label_dv: "ފޮޓޯ", widget: "toggle",
    unit: "", values: [], min: null, max: null, histogram: [], count_true: 7 },
  { key: "announcement_type", label: "Type", label_dv: "ބާވަތް", widget: "checkbox",
    unit: "", values: [{ value: "ބީލަން", label: "ބީލަން", count: 4 }],
    min: null, max: null, histogram: [], count_true: null },
] as never;

const state = { q: "phone", page: 1, per_page: 20, sort: "relevance", f: [] as string[] };

describe("FacetPanel", () => {
  it("renders one pill per facet, in the order given", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getByRole("button", { name: "Brand" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Price" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Has photos/ })).toBeInTheDocument();
  });

  it("opens the checkbox options on click, with the count next to each value", async () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Brand" }));
    expect(screen.getByLabelText(/Apple/)).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("emits a new state when a checkbox is ticked", async () => {
    const onChange = vi.fn();
    render(<FacetPanel facets={facets} state={state} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Brand" }));
    await userEvent.click(screen.getByLabelText(/Apple/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ f: ["brand:Apple"] })
    );
  });

  it("reflects an already-applied filter as checked, and summarizes it on the pill", async () => {
    render(
      <FacetPanel facets={facets} state={{ ...state, f: ["brand:Apple"] }}
                  onChange={() => {}} />
    );
    expect(screen.getByRole("button", { name: "Brand: Apple" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Brand: Apple" }));
    expect(screen.getByLabelText(/Apple/)).toBeChecked();
  });

  it("is a direct on/off pill for a toggle facet, with its true-count", async () => {
    const onChange = vi.fn();
    render(<FacetPanel facets={facets} state={state} onChange={onChange} />);
    const pill = screen.getByRole("button", { name: /Has photos/ });
    expect(pill).toHaveTextContent("7");
    await userEvent.click(pill);
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ f: ["has_images:true"] })
    );
  });

  it("opens the range histogram and unit on click, as bars rather than a table", async () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Price" }));
    expect(screen.getAllByTestId("hist-bar")).toHaveLength(2);
    expect(screen.getByText(/MVR/)).toBeInTheDocument();
  });

  it("offers a clear action per pill only when that facet is active", () => {
    const { rerender } = render(
      <FacetPanel facets={facets} state={state} onChange={() => {}} />
    );
    expect(screen.queryByRole("button", { name: /clear brand/i })).toBeNull();
    rerender(
      <FacetPanel facets={facets} state={{ ...state, f: ["brand:Apple"] }}
                  onChange={() => {}} />
    );
    expect(screen.getByRole("button", { name: /clear brand/i })).toBeInTheDocument();
  });

  it("clears the filter from the pill without opening its dropdown", async () => {
    const onChange = vi.fn();
    render(
      <FacetPanel facets={facets} state={{ ...state, f: ["brand:Apple"] }}
                  onChange={onChange} />
    );
    await userEvent.click(screen.getByRole("button", { name: /clear brand/i }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ f: [] }));
    expect(screen.queryByLabelText(/Apple/)).toBeNull();
  });

  it("gives a Dhivehi option its own dir inside the checkbox dropdown", async () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Type" }));
    expect(screen.getByText("ބީލަން")).toHaveAttribute("dir", "rtl");
  });

  it("gives a Dhivehi value its own dir in the pill summary, without flipping the label", async () => {
    render(
      <FacetPanel
        facets={facets}
        state={{ ...state, f: ["announcement_type:ބީލަން"] }}
        onChange={() => {}}
      />
    );
    const pill = screen.getByRole("button", { name: "Type: ބީލަން" });
    expect(screen.getByText("ބީލަން")).toHaveAttribute("dir", "rtl");
    expect(pill).not.toHaveAttribute("dir");
  });

  it("renders nothing when there are no facets", () => {
    const { container } = render(
      <FacetPanel facets={[] as never} state={state} onChange={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });
});
