import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Bidi } from "@/components/Bidi";
import { Disclosure } from "./Disclosure";

describe("Disclosure", () => {
  it("is collapsed until asked", () => {
    render(<Disclosure label="More details"><p>hidden thing</p></Disclosure>);
    expect(screen.queryByText("hidden thing")).toBeNull();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "false");
  });

  it("expands in place and reports state", async () => {
    render(<Disclosure label="More details"><p>hidden thing</p></Disclosure>);
    await userEvent.click(screen.getByRole("button", { name: /more details/i }));
    expect(screen.getByText("hidden thing")).toBeInTheDocument();
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("renders no overlay, portal or dialog", () => {
    const { container, baseElement } = render(
      <Disclosure label="More"><p>x</p></Disclosure>
    );
    expect(baseElement.querySelector('[role="dialog"]')).toBeNull();
    expect(baseElement.querySelector("dialog")).toBeNull();
    expect(baseElement.children).toHaveLength(1);   // nothing portalled out
    expect(container.contains(screen.getByRole("button"))).toBe(true);
  });

  it("does not lock scrolling", async () => {
    render(<Disclosure label="More"><p>x</p></Disclosure>);
    await userEvent.click(screen.getByRole("button", { name: /more/i }));
    expect(document.body.style.overflow).toBe("");
  });

  it("labels its content region for assistive technology", async () => {
    render(<Disclosure label="More details"><p>x</p></Disclosure>);
    const btn = screen.getByRole("button");
    await userEvent.click(btn);
    const id = btn.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(document.getElementById(id!)).toContainElement(screen.getByText("x"));
  });

  it("renders full width rather than as an inline text link", () => {
    const { container } = render(
      <Disclosure label="Details"><p>body</p></Disclosure>
    );
    expect(container.firstElementChild).toHaveClass("collapse");
  });

  it("has no native details marker to mis-flip under rtl", () => {
    render(<Disclosure label="Details" defaultOpen><p>body</p></Disclosure>);
    expect(document.querySelector("details")).toBeNull();
    expect(document.querySelector("summary")).toBeNull();
  });

  it("keeps latin text ltr inside an rtl container", () => {
    render(
      <div dir="rtl">
        <Disclosure label="Details" defaultOpen>
          <Bidi text="RoseWare Corporation Pvt Ltd" as="p" />
        </Disclosure>
      </div>
    );
    expect(screen.getByText("RoseWare Corporation Pvt Ltd"))
      .toHaveAttribute("dir", "ltr");
  });
});
