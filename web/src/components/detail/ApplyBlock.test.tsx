import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApplyBlock } from "./ApplyBlock";

const methods = [
  { kind: "form", value: "https://forms.gle/abc", label_en: "", label_dv: "" },
  { kind: "email", value: "hr@example.gov.mv", label_en: "", label_dv: "" },
  { kind: "phone", value: "7994400", label_en: "", label_dv: "" },
  { kind: "viber", value: "9223232", label_en: "", label_dv: "" },
];

describe("ApplyBlock", () => {
  it("renders a form link as a button", () => {
    render(<ApplyBlock methods={methods} />);
    const a = screen.getByRole("link", { name: /apply via form/i });
    expect(a).toHaveAttribute("href", "https://forms.gle/abc");
    expect(a).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("renders the form apply method as a block button", () => {
    render(<ApplyBlock methods={methods} />);
    expect(screen.getByRole("link", { name: /apply via form/i })).toHaveClass("btn-block");
  });

  it("prefers the model's own label over the generic kind label", () => {
    render(<ApplyBlock methods={[
      { kind: "email", value: "https://mtcc.mv/careers/", label_en: "Company website" },
    ]} />);
    expect(screen.getByRole("link", { name: "Company website" })).toBeInTheDocument();
  });

  it("prefers label_dv over label_en when dv is set", () => {
    render(<ApplyBlock dv methods={[
      { kind: "email", value: "hr@example.gov.mv", label_en: "Company website",
        label_dv: "ކުންފުނީގެ ވެބްސައިޓް" },
    ]} />);
    expect(screen.getByRole("link", { name: "ކުންފުނީގެ ވެބްސައިޓް" })).toBeInTheDocument();
  });

  it("falls back to label_en when dv is set but label_dv is not yet translated", () => {
    render(<ApplyBlock dv methods={[
      { kind: "email", value: "hr@example.gov.mv", label_en: "Company website" },
    ]} />);
    expect(screen.getByRole("link", { name: "Company website" })).toBeInTheDocument();
  });

  it("does not wire up mailto: for a URL enrichment mistakenly tagged as email", () => {
    render(<ApplyBlock methods={[
      { kind: "email", value: "https://mtcc.mv/careers/", label_en: "Company website" },
    ]} />);
    const a = screen.getByRole("link", { name: "Company website" });
    expect(a).toHaveAttribute("href", "https://mtcc.mv/careers/");
    expect(a).toHaveAttribute("target", "_blank");
  });

  it("renders email as mailto", () => {
    render(<ApplyBlock methods={methods} />);
    expect(screen.getByRole("link", { name: /hr@example/i }))
      .toHaveAttribute("href", "mailto:hr@example.gov.mv");
  });

  it("renders a phone number as tap-to-call with the country code", () => {
    render(<ApplyBlock methods={methods} />);
    expect(screen.getByRole("link", { name: /7994400/ }))
      .toHaveAttribute("href", "tel:+9607994400");
  });

  it("renders a Viber number as a viber deep link", () => {
    render(<ApplyBlock methods={methods} />);
    expect(screen.getByRole("link", { name: /9223232/ })
      .getAttribute("href")).toMatch(/^viber:/);
  });

  it("renders nothing when there are no methods", () => {
    const { container } = render(<ApplyBlock methods={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
