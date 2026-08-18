import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Bidi } from "./Bidi";

describe("Bidi", () => {
  it("marks Thaana content rtl and lang=dv on the element itself", () => {
    render(<Bidi as="h2" text="ވަޒީފާގެ ފުރުޞަތު" data-testid="t" />);
    const el = screen.getByTestId("t");
    expect(el).toHaveAttribute("dir", "rtl");
    expect(el).toHaveAttribute("lang", "dv");
  });

  it("marks Latin content ltr and lang=en", () => {
    render(<Bidi as="h2" text="Administrative Officer" data-testid="t" />);
    expect(screen.getByTestId("t")).toHaveAttribute("dir", "ltr");
  });

  it("two siblings of different scripts each carry their own dir", () => {
    render(
      <div data-testid="row">
        <Bidi as="span" text="Gazette" data-testid="a" />
        <Bidi as="span" text="ގެޒެޓް" data-testid="b" />
      </div>
    );
    expect(screen.getByTestId("a")).toHaveAttribute("dir", "ltr");
    expect(screen.getByTestId("b")).toHaveAttribute("dir", "rtl");
    // The container must NOT have been flipped -- spec 10 forbids page-level
    // and container-level flipping on mixed content.
    expect(screen.getByTestId("row")).not.toHaveAttribute("dir");
  });

  it("renders nothing for empty text rather than an empty element", () => {
    const { container } = render(<Bidi as="p" text="" />);
    expect(container.firstChild).toBeNull();
  });
});
