import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  jobFloorEstimate, jobNegotiable, jobResult, jobUnlisted, dhivehiTitleResult,
} from "@/test/fixtures";
import { JobCard } from "./JobCard";

describe("JobCard", () => {
  it("leads with role, employer and salary", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByText("Administrative Officer")).toBeInTheDocument();
    expect(screen.getByText("Ministry of Example")).toBeInTheDocument();
    expect(screen.getByText("MVR 10,750 / month")).toBeInTheDocument();
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

  it("renders the take-home estimate as explicitly approximate", () => {
    render(<JobCard result={jobResult} />);
    const est = screen.getByTestId("net-estimate");
    expect(est.textContent).toMatch(/~/);
    expect(est.textContent).toMatch(/14,397|14,398/);
  });

  it("renders a partial estimate as a floor, never as a point value", () => {
    render(<JobCard result={jobFloorEstimate} />);
    expect(screen.getByTestId("net-estimate").textContent).toMatch(/at least/i);
  });

  it("omits the estimate entirely when there is none", () => {
    render(<JobCard result={jobUnlisted} />);
    expect(screen.queryByTestId("net-estimate")).toBeNull();
  });

  it("exposes the estimate's assumptions without leaving the card", async () => {
    render(<JobCard result={jobResult} />);
    await userEvent.click(screen.getByRole("button", { name: /assumptions/i }));
    expect(screen.getByText(/20 working days/i)).toBeInTheDocument();
    expect(screen.getByText(/7%/)).toBeInTheDocument();
  });

  it("says when the details came out of an attachment", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByText(/from attached/i)).toBeInTheDocument();
  });

  it("renders an icon row for the apply methods", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByLabelText(/apply via form/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/apply by email/i)).toBeInTheDocument();
  });

  it("renders the deadline state the server computed, not one of its own", () => {
    render(<JobCard result={jobResult} />);
    expect(screen.getByTestId("deadline").textContent).toMatch(/31/);
  });

  it("gives a Thaana role its own dir", () => {
    render(<JobCard result={dhivehiTitleResult} />);
    expect(screen.getByText("ވަޒީފާގެ ފުރުޞަތު")).toHaveAttribute("dir", "rtl");
  });
});
