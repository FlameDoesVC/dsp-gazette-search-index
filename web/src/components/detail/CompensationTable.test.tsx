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

  it("shows the arithmetic the user can follow", () => {
    render(<CompensationTable comp={comp} />);
    expect(screen.getByText(/pension/i)).toBeInTheDocument();
    expect(screen.getByText(/-560/)).toBeInTheDocument();
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
