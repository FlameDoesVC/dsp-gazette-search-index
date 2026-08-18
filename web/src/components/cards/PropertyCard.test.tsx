import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { propertyBedSpace, propertyRoomOfThree } from "@/test/fixtures";
import { PropertyCard } from "./PropertyCard";

describe("PropertyCard", () => {
  it("shows location, rent and capacity at a glance", () => {
    render(<PropertyCard result={propertyRoomOfThree} />);
    expect(screen.getByText("Hulhumale Phase 2")).toBeInTheDocument();
    expect(screen.getByText("MVR 7,000 / month")).toBeInTheDocument();
    expect(screen.getByText("1 room of 3, shared")).toBeInTheDocument();
  });

  it("NEVER renders one room of three as a three-bedroom unit", () => {
    // The concrete failure spec 8.2 exists to prevent. `bedrooms: 3` is in the
    // payload and must not become the headline capacity.
    render(<PropertyCard result={propertyRoomOfThree} />);
    const capacity = screen.getByTestId("capacity");
    expect(capacity.textContent).toBe("1 room of 3, shared");
    expect(capacity.textContent).not.toMatch(/3 bedroom/i);
  });

  it("renders bed-space capacity as given", () => {
    render(<PropertyCard result={propertyBedSpace} />);
    expect(screen.getByTestId("capacity").textContent)
      .toBe("Bed space, 2 available, shared");
  });

  it("shows a hero image with a count when there are several", () => {
    const { container } = render(<PropertyCard result={propertyRoomOfThree} />);
    expect(container.querySelector("img")).toHaveAttribute("src", "https://x/1.jpg");
    expect(screen.getByText(/4/)).toBeInTheDocument();
  });

  it("renders a placeholder rather than a broken image when there is none", () => {
    render(<PropertyCard result={propertyBedSpace} />);
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByTestId("no-image")).toBeInTheDocument();
  });

  it("renders tenant preference as chips", () => {
    render(<PropertyCard result={propertyBedSpace} />);
    expect(screen.getByText("Male")).toBeInTheDocument();
    expect(screen.getByText("Working")).toBeInTheDocument();
  });

  it("marks an inferred currency differently from a stated one", () => {
    const inferred = {
      ...propertyRoomOfThree,
      card: { ...propertyRoomOfThree.card, currency_inferred: true },
    } as never;
    render(<PropertyCard result={inferred} />);
    expect(screen.getByTitle(/currency inferred/i)).toBeInTheDocument();
  });
});
