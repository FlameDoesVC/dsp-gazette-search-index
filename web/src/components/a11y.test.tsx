import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetaContext } from "./MetaProvider";
import { ResultList } from "./ResultList";
import {
  jobResult, newsResult, propertyRoomOfThree, shoppingResult,
} from "@/test/fixtures";

const meta = {
  tabs: [], doc_types: [], sorts: [],
  sources: [
    { key: "gazette", label_en: "Gazette", label_dv: "ގެޒެޓް",
      icon: "/sources/gazette.svg", icon_fallback_text: "ގ", accent: "",
      site_url: "https://gazette.gov.mv" },
    { key: "ibay", label_en: "iBay", label_dv: "އައިބޭ",
      icon: "/sources/ibay.svg", icon_fallback_text: "iB", accent: "",
      site_url: "https://ibay.com.mv" },
  ],
};

const all = [jobResult, propertyRoomOfThree, shoppingResult, newsResult];

function renderAll() {
  return render(
    <MetaContext.Provider value={meta as never}>
      <ResultList results={all} queryId={1} />
    </MetaContext.Provider>
  );
}

describe("cross-cutting", () => {
  it("every card carries a source badge", () => {
    renderAll();
    expect(screen.getAllByText(/Gazette|iBay/).length).toBe(all.length);
  });

  it("no card places its badge with a physical left/right property", () => {
    const { container } = renderAll();
    const offenders = Array.from(container.querySelectorAll("*")).filter((el) =>
      /(^|\s)(ml-|mr-|left-|right-|text-left|text-right)/.test(el.className || "")
    );
    // Physical properties do not flip with dir and are the standard way RTL
    // layouts break. Use ms-/me-/start-/end-/text-start/text-end instead.
    expect(offenders.map((e) => e.className)).toEqual([]);
  });

  it("every image has an alt attribute", () => {
    const { container } = renderAll();
    container.querySelectorAll("img").forEach((img) =>
      expect(img).toHaveAttribute("alt")
    );
  });

  it("every heading is an h3 inside a result card, so the page outline is sane", () => {
    renderAll();
    screen.getAllByRole("heading").forEach((h) =>
      expect(h.tagName).toBe("H3")
    );
  });

  it("no external anchor is missing noopener noreferrer", () => {
    const { container } = renderAll();
    container.querySelectorAll('a[target="_blank"]').forEach((a) => {
      expect(a.getAttribute("rel")).toContain("noopener");
      expect(a.getAttribute("rel")).toContain("noreferrer");
    });
  });

  it("no component renders a modal, dialog or portal", () => {
    const { baseElement } = renderAll();
    expect(baseElement.querySelector('[role="dialog"]')).toBeNull();
    expect(baseElement.querySelector('[aria-modal="true"]')).toBeNull();
    expect(baseElement.querySelector("dialog")).toBeNull();
  });
});
