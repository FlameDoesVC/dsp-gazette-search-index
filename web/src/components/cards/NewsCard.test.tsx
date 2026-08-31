import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { dhivehiNewsResult, newsResult } from "@/test/fixtures";
import { NewsCard } from "./NewsCard";

describe("NewsCard", () => {
  it("is a single outbound anchor to the source", () => {
    render(<NewsCard result={newsResult} />);
    const a = screen.getByRole("link");
    expect(a).toHaveAttribute("href", "https://gazette.gov.mv/iulaan/5");
    expect(a).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(a).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
  });

  it("does NOT link to an internal detail route", () => {
    // Spec 8.4: no detail page. Building an internal reader for content we do
    // not own is work that helps nobody.
    render(<NewsCard result={newsResult} />);
    expect(screen.getByRole("link").getAttribute("href"))
      .not.toMatch(/^\/documents\//);
  });

  it("carries the excerpt, which is the whole product for a news result", () => {
    render(<NewsCard result={newsResult} />);
    expect(screen.getByText(/sealed bids for harbour/i)).toBeInTheDocument();
  });

  it("shows the office and attachment count", () => {
    render(<NewsCard result={newsResult} />);
    expect(screen.getByText("Ministry of Example")).toBeInTheDocument();
    expect(screen.getByText(/2 documents/i)).toBeInTheDocument();
  });

  it("shows the announcement type label in English for an English-titled item", () => {
    render(<NewsCard result={newsResult} />);
    expect(screen.getByText("Tender")).toBeInTheDocument();
  });

  it("gives the Thaana announcement type label its own dir for a Dhivehi-titled item", () => {
    render(<NewsCard result={dhivehiNewsResult} />);
    expect(screen.getByText("ބީލަން")).toHaveAttribute("dir", "rtl");
  });

  it("shows no translated flag when nothing fell back", () => {
    render(<NewsCard result={newsResult} />);
    expect(screen.queryByText("Translated")).toBeNull();
  });

  it("flags a result shown in the other language", () => {
    render(<NewsCard result={{ ...newsResult, translated: true }} />);
    expect(screen.getByText("Translated")).toBeInTheDocument();
  });
});
