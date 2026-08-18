import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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
});
