/**
 * SPIKE, throwaway. Does a daisyui collapse survive this project's constraints?
 *
 * Three questions, in order of what would kill the migration:
 *   1. does it still satisfy the a11y contract the existing Disclosure has
 *   2. does per-element dir still work INSIDE it, with mixed scripts
 *   3. does it stay an inline disclosure rather than becoming an overlay
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Bidi } from "@/components/Bidi";
import { DisclosureSpike } from "@/components/DisclosureSpike";

const THAANA = "ފެނަކަ ކޯޕަރޭޝަން ލިމިޓެޑް";
const LATIN = "RoseWare Corporation Pvt Ltd";

describe("daisyui collapse spike", () => {
  it("is collapsed by default and reveals on click", async () => {
    render(
      <DisclosureSpike label="Details">
        <p>PC-171/2026/T327</p>
      </DisclosureSpike>
    );
    const button = screen.getByRole("button", { name: "Details" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("PC-171/2026/T327")).toBeVisible();
  });

  it("keeps the button/region relationship, not a native details marker", () => {
    render(
      <DisclosureSpike label="Details" defaultOpen>
        <p>body</p>
      </DisclosureSpike>
    );
    const button = screen.getByRole("button", { name: "Details" });
    const id = button.getAttribute("aria-controls");
    expect(id).toBeTruthy();
    expect(document.getElementById(id as string)).not.toBeNull();
    // A <details>/<summary> collapse would put a marker here that ignores dir.
    expect(document.querySelector("details")).toBeNull();
    expect(document.querySelector("summary")).toBeNull();
  });

  it("renders mixed scripts with direction per element inside the collapse", () => {
    render(
      <DisclosureSpike label="Details" defaultOpen>
        <Bidi text={THAANA} as="p" />
        <Bidi text={LATIN} as="p" />
      </DisclosureSpike>
    );
    expect(screen.getByText(THAANA)).toHaveAttribute("dir", "rtl");
    expect(screen.getByText(LATIN)).toHaveAttribute("dir", "ltr");
  });

  it("works inside an RTL container without flipping its LTR children", () => {
    render(
      <div dir="rtl">
        <DisclosureSpike label="Details" defaultOpen>
          <Bidi text={LATIN} as="p" />
        </DisclosureSpike>
      </div>
    );
    expect(screen.getByText(LATIN)).toHaveAttribute("dir", "ltr");
  });

  it("is not a modal, dialog or portal", () => {
    const { baseElement } = render(
      <DisclosureSpike label="Details" defaultOpen>
        <p>body</p>
      </DisclosureSpike>
    );
    expect(baseElement.querySelector('[role="dialog"]')).toBeNull();
    expect(baseElement.querySelector('[aria-modal="true"]')).toBeNull();
    expect(baseElement.querySelector("dialog")).toBeNull();
  });
});
