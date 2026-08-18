import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MetaContext } from "./MetaProvider";
import { SourceBadge } from "./SourceBadge";

const meta = {
  tabs: [],
  doc_types: [],
  sorts: [],
  sources: [
    { key: "gazette", label_en: "Gazette", label_dv: "ގެޒެޓް",
      icon: "/sources/gazette.svg", icon_fallback_text: "ގ", accent: "",
      site_url: "https://gazette.gov.mv" },
    { key: "nofavicon", label_en: "No Favicon", label_dv: "", icon: "",
      icon_fallback_text: "NF", accent: "", site_url: "https://x" },
  ],
};

const wrap = (ui: React.ReactNode) => (
  <MetaContext.Provider value={meta as never}>{ui}</MetaContext.Provider>
);

describe("SourceBadge", () => {
  const img = (container: HTMLElement) => container.querySelector("img");

  it("renders the self-hosted icon, never a third-party favicon URL", () => {
    const { container } = render(wrap(<SourceBadge sourceKey="gazette" />));
    expect(img(container)!.getAttribute("src")).toBe("/sources/gazette.svg");
    expect(img(container)!.getAttribute("src")).not.toContain("gazette.gov.mv");
  });

  it("always pairs the icon with a label -- never a bare rebus", () => {
    render(wrap(<SourceBadge sourceKey="gazette" />));
    expect(screen.getByText("Gazette")).toBeInTheDocument();
  });

  it("falls back to a monogram chip when a source has no usable icon", () => {
    render(wrap(<SourceBadge sourceKey="nofavicon" />));
    expect(screen.queryByRole("img", { hidden: true })).toBeNull();
    expect(screen.getByText("NF")).toBeInTheDocument();
  });

  it("renders the Dhivehi label with its own dir when lang is dv", () => {
    render(wrap(<SourceBadge sourceKey="gazette" lang="dv" />));
    expect(screen.getByText("ގެޒެޓް")).toHaveAttribute("dir", "rtl");
  });

  it("renders nothing for an unknown source key rather than a broken chip", () => {
    const { container } = render(wrap(<SourceBadge sourceKey="mystery" />));
    expect(container.firstChild).toBeNull();
  });

  it("uses 16px in dense contexts and 20px on cards", () => {
    const { container, rerender } = render(
      wrap(<SourceBadge sourceKey="gazette" size="sm" />)
    );
    expect(img(container)).toHaveAttribute("width", "16");
    rerender(wrap(<SourceBadge sourceKey="gazette" size="md" />));
    expect(img(container)).toHaveAttribute("width", "20");
  });
});
