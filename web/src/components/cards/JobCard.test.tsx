import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  jobFloorEstimate, jobNegotiable, jobResult, jobUnlisted, dhivehiTitleResult,
  dhivehiTitleEnglishRoleResult, dhivehiJobFreeTextResult,
} from "@/test/fixtures";
import { JobCard } from "./JobCard";

describe("JobCard", () => {
  it("leads with role, employer and the take-home estimate when there is one", () => {
    // The stated basic salary (MVR 10,750 / month) is not the headline once
    // there is a take-home figure to show instead -- it stays reachable in
    // Details via CompensationTable, but the card leads with what a
    // candidate actually cares about.
    render(<JobCard result={jobResult} />);
    expect(screen.getByText("Administrative Officer")).toBeInTheDocument();
    expect(screen.getByText("Ministry of Example")).toBeInTheDocument();
    expect(screen.getByTestId("net-estimate").textContent).toMatch(/\/ month/);
    expect(screen.queryByText("MVR 10,750 / month")).toBeNull();
  });

  it("renders salary_display verbatim rather than interpreting a null", () => {
    render(<JobCard result={jobUnlisted} />);
    expect(screen.getByText("Unlisted")).toBeInTheDocument();
  });

  it("shows Negotiable only when the payload says so", () => {
    render(<JobCard result={jobNegotiable} />);
    expect(screen.getByText("Negotiable")).toBeInTheDocument();
    expect(screen.queryByText("Unlisted")).toBeNull();
  });

  it("renders the take-home estimate as explicitly approximate, with its currency", () => {
    render(<JobCard result={jobResult} />);
    const est = screen.getByTestId("net-estimate");
    expect(est.textContent).toMatch(/~/);
    expect(est.textContent).toMatch(/14,397|14,398/);
    expect(est.textContent).toMatch(/MVR/);
  });

  it("renders a partial estimate the same as any other, without an 'at least' qualifier", () => {
    render(<JobCard result={jobFloorEstimate} />);
    expect(screen.getByTestId("net-estimate").textContent).not.toMatch(/at least/i);
  });

  it("omits the estimate entirely when there is none", () => {
    render(<JobCard result={jobUnlisted} />);
    expect(screen.queryByTestId("net-estimate")).toBeNull();
  });

  it("shows the position type label in English for an English role", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByText("Permanent")).toBeInTheDocument();
  });

  it("shows the position type label in Dhivehi, RTL, for a Dhivehi role", () => {
    render(<JobCard result={dhivehiTitleResult} />);
    expect(screen.getByText("ދާއިމީ")).toHaveAttribute("dir", "rtl");
  });

  it("flips the required-documents/pay-breakdown grid rtl for a Dhivehi document, so columns swap sides", () => {
    const { container } = render(<JobCard result={dhivehiJobFreeTextResult} />);
    const grid = container.querySelector(".lg\\:grid");
    expect(grid).toHaveAttribute("dir", "rtl");
  });

  it("keeps the required-documents/pay-breakdown grid ltr for an English document", () => {
    const { container } = render(<JobCard result={jobResult} />);
    const grid = container.querySelector(".lg\\:grid");
    expect(grid).toHaveAttribute("dir", "ltr");
  });

  it("shows the take-home estimate in Dhivehi currency form, amount before the per-month word", () => {
    render(<JobCard result={dhivehiTitleResult} />);
    const est = screen.getByTestId("net-estimate");
    expect(est.textContent).toBe("~14,398ރ މަހަކު");
    expect(est).toHaveAttribute("dir", "rtl");
  });

  it("shows the employer in Dhivehi, RTL, when translated and the document is Dhivehi", () => {
    render(<JobCard result={dhivehiJobFreeTextResult} />);
    expect(screen.getByText("ދިވެހިރާއްޖޭގެ ޤައުމީ ޔުނިވަރސިޓީ")).toHaveAttribute("dir", "rtl");
    expect(screen.queryByText("The Maldives National University")).toBeNull();
  });

  it("shows each qualification/required document in Dhivehi when translated, falling back per item otherwise", () => {
    render(<JobCard result={dhivehiJobFreeTextResult} />);
    expect(screen.getByText("ގުޅުންހުރި ދާއިރާއަކުން ޑިގްރީއެއް")).toBeInTheDocument();
    // The second qualification has no _dv sibling yet in the fixture.
    expect(screen.getByText("Two years experience")).toBeInTheDocument();
    expect(screen.getByText("އަޕްޑޭޓް ކުރެވިފައިވާ ސީވީ")).toBeInTheDocument();
  });

  it("says when the details came out of an attachment", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByText(/from attached/i)).toBeInTheDocument();
  });

  it("renders the deadline state the server computed, not one of its own", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByTestId("deadline").textContent).toMatch(/31/);
  });

  it("gives a Thaana role its own dir", () => {
    render(<JobCard result={dhivehiTitleResult} />);
    expect(screen.getByText("ވަޒީފާގެ ފުރުޞަތު")).toHaveAttribute("dir", "rtl");
  });

  it("shows the resolved Dhivehi title, not an English role that carries no language guarantee", () => {
    render(<JobCard result={dhivehiTitleEnglishRoleResult} />);
    expect(screen.getByText("ލެބޯޓްރީ ޓެކްނީޝަން")).toHaveAttribute("dir", "rtl");
    expect(screen.queryByText("Laboratory Technician")).toBeNull();
  });

  it("renders the deadline label and month in Dhivehi for a Dhivehi role", () => {
    render(<JobCard result={dhivehiTitleResult} />);
    const deadline = screen.getByTestId("deadline");
    expect(deadline).toHaveAttribute("dir", "rtl");
    expect(deadline.textContent).toContain("ސުންގަޑި");
    expect(deadline.textContent).toContain("އޯގަސްޓް");
    expect(deadline.textContent).not.toMatch(/Closes/);
  });

  it("flags a result shown in the other language, so a fallback doesn't read as wrong", () => {
    render(<JobCard result={dhivehiTitleResult} />);
    expect(screen.getByText("Translated")).toBeInTheDocument();
  });

  it("shows no translated flag when the title matched the response language natively", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.queryByText("Translated")).toBeNull();
  });

  it("shows its detail inline, always, rather than behind a toggle or an overlay", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByText(/basic medical degree/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /apply via form/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /details/i })).toBeNull();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("shows the summary instead when there are no details to show", () => {
    const bare = {
      ...jobResult,
      card: {
        source: "gazette", role: "Administrative Officer",
        salary_display: "Unlisted",
      },
    } as never;
    render(<JobCard result={bare} />);
    expect(screen.queryByRole("button", { name: /details/i })).toBeNull();
    expect(screen.getByText(jobResult.summary)).toBeInTheDocument();
  });

  it("shows details instead of the summary when there are details to show", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.queryByText(jobResult.summary)).toBeNull();
  });
});
