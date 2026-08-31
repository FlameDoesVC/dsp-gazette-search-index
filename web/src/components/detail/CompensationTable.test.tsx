import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CompensationTable } from "./CompensationTable";

const comp = {
  basic_salary: 8000, basic_salary_max: null, currency: "MVR",
  period: "month" as const, pension_applies: true, pension_rate: 0.07,
  salary_state: "listed" as const, completeness: "full" as const,
  allowances: [{ kind: "attendance", label_raw: "ހާޒިރީ އެލަވަންސް", amount: 100,
                 basis: "per_day" as const }],
};

describe("CompensationTable", () => {
  it("shows every line item the employer stated", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByText(/8,000/)).toBeInTheDocument();
    expect(screen.getByText("ހާޒިރީ އެލަވަންސް")).toHaveAttribute("dir", "rtl");
  });

  it("prefers label_dv over label_raw when dv is set", () => {
    const withDv = {
      ...comp,
      allowances: [{ kind: "attendance", label_raw: "Attendance Allowance",
                    label_dv: "ހާޒިރީ އެލަވަންސް", amount: 100,
                    basis: "per_day" as const }],
    };
    render(<CompensationTable comp={withDv} dv />);
    expect(screen.getByText("ހާޒިރީ އެލަވަންސް")).toBeInTheDocument();
    expect(screen.queryByText("Attendance Allowance")).toBeNull();
  });

  it("falls back to label_raw when dv is set but label_dv is not yet translated", () => {
    const noDv = {
      ...comp,
      allowances: [{ kind: "attendance", label_raw: "Attendance Allowance",
                    amount: 100, basis: "per_day" as const }],
    };
    render(<CompensationTable comp={noDv} dv />);
    expect(screen.getByText("Attendance Allowance")).toBeInTheDocument();
  });

  it("translates the static headings and cell text when dv is set, with dir on the cell itself", () => {
    render(<CompensationTable comp={comp} dv />);
    expect(screen.getByText("މުސާރައިގެ ތަފުސީލު")).toHaveAttribute("dir", "rtl");
    const basicSalaryCell = screen.getByText("އަސާސީ މުސާރަ");
    expect(basicSalaryCell.tagName).toBe("TD");
    expect(basicSalaryCell).toHaveAttribute("dir", "rtl");
    expect(screen.queryByText("Basic salary")).toBeNull();
    expect(screen.queryByText("Pay breakdown")).toBeNull();
  });

  it("shows the take-home control and pension line in Dhivehi when dv is set", () => {
    render(<CompensationTable comp={comp} dv />);
    expect(screen.getByLabelText(/މަސައްކަތު ދުވަހަށް/)).toBeInTheDocument();
    expect(screen.getByText(/ޕެންޝަން/)).toBeInTheDocument();
  });

  it("flips the table itself rtl when dv is set, so the amount column lands on the other side", () => {
    const { container } = render(<CompensationTable comp={comp} dv />);
    expect(container.querySelector("table")).toHaveAttribute("dir", "rtl");
  });

  it("keeps the table ltr when dv is not set", () => {
    const { container } = render(<CompensationTable comp={comp} />);
    expect(container.querySelector("table")).toHaveAttribute("dir", "ltr");
  });

  it("shows every amount in Dhivehi currency form (suffixed symbol, no space) when dv is set", () => {
    render(<CompensationTable comp={comp} dv />);
    expect(screen.getByText("8,000ރ")).toBeInTheDocument();
    expect(screen.getByTestId("net-total").textContent).toMatch(/^~[\d,]+ރ$/);
  });

  it("shows the no-estimate message in Dhivehi when dv is set", () => {
    const noEstimate = { ...comp, basic_salary: null };
    render(<CompensationTable comp={noEstimate} dv />);
    expect(screen.getByText(/ތަފުސީލެއް/)).toBeInTheDocument();
  });

  it("carries the currency on every amount, not just the basic salary", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByText(/MVR\s*8,000/)).toBeInTheDocument();
    expect(screen.getByText(/MVR\s*100/)).toBeInTheDocument(); // the allowance
    expect(screen.getByText(/MVR\s*-?560/)).toBeInTheDocument(); // the pension
  });

  it("shows the arithmetic the user can follow", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByText(/pension/i)).toBeInTheDocument();
    expect(screen.getByText(/-560/)).toBeInTheDocument();
  });

  it("shows a percent_of_basic allowance's percentage as subtext and its computed amount as the figure", () => {
    const withPercent = {
      ...comp,
      allowances: [{ kind: "risk", label_raw: "Risk allowance", amount: 5,
                    basis: "percent_of_basic" as const }],
    };
    render(<CompensationTable comp={withPercent} />);
    expect(screen.getByText("5% of basic")).toBeInTheDocument();
    // 5% of 8,000 basic salary is 400 -- the amount column, not the raw "5".
    expect(screen.getByText(/MVR\s*400/)).toBeInTheDocument();
    expect(screen.queryByText(/^5$/)).toBeNull();
  });

  it("recomputes client-side when the working-days control changes", async () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByTestId("net-total").textContent).toMatch(/9,440/);
    const control = screen.getByLabelText(/working days/i);
    await userEvent.clear(control);
    await userEvent.type(control, "26");
    expect(screen.getByTestId("net-total").textContent).toMatch(/10,040/);
  });

  it("labels the total as an estimate, not as pay", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByTestId("net-total").textContent).toMatch(/estimate|~/i);
  });

  it("rejects a working-days value outside 1..31 rather than computing nonsense",
     async () => {
    render(<CompensationTable comp={comp} />);
    const control = screen.getByLabelText(/working days/i);
    await userEvent.clear(control);
    await userEvent.type(control, "400");
    expect(control).toHaveAttribute("max", "31");
    expect(screen.getByTestId("net-total").textContent).not.toMatch(/40,000/);
  });
});
