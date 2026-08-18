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
] as never;

const state = { q: "phone", type: "shopping", page: 1, per_page: 20,
                sort: "relevance", f: [] as string[] };

describe("FacetPanel", () => {
  it("renders one control per facet, in the order given", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["Brand", "Price", "Has photos"]);
  });

  it("shows the count next to each checkbox value", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getByLabelText(/Apple/)).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("emits a new state when a checkbox is ticked", async () => {
    const onChange = vi.fn();
    render(<FacetPanel facets={facets} state={state} onChange={onChange} />);
    await userEvent.click(screen.getByLabelText(/Apple/));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ f: ["brand:Apple"] })
    );
  });

  it("reflects an already-applied filter as checked", () => {
    render(
      <FacetPanel facets={facets} state={{ ...state, f: ["brand:Apple"] }}
                  onChange={() => {}} />
    );
    expect(screen.getByLabelText(/Apple/)).toBeChecked();
  });

  it("renders a toggle with its true-count", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getByLabelText(/Has photos/)).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("renders the range histogram as bars, not as a table", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getAllByTestId("hist-bar")).toHaveLength(2);
  });

  it("shows the unit on a range facet", () => {
    render(<FacetPanel facets={facets} state={state} onChange={() => {}} />);
    expect(screen.getByText(/MVR/)).toBeInTheDocument();
  });

  it("offers a clear action per facet only when that facet is active", async () => {
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

  it("renders nothing when there are no facets", () => {
    const { container } = render(
      <FacetPanel facets={[] as never} state={state} onChange={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });
});
