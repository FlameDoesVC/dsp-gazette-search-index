import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { shoppingResult } from "@/test/fixtures";
import { ShoppingCard } from "./ShoppingCard";

describe("ShoppingCard", () => {
  it("is image-forward with price and title", () => {
    const { container } = render(<ShoppingCard result={shoppingResult} />);
    expect(container.querySelector("img")).toHaveAttribute("src", "https://x/ps.jpg");
    expect(screen.getByText("MVR 850")).toBeInTheDocument();
  });

  it("renders up to three spec chips", () => {
    render(<ShoppingCard result={shoppingResult} />);
    ["24V", "5A", "120W"].forEach((c) =>
      expect(screen.getByText(c)).toBeInTheDocument()
    );
  });

  it("badges the condition", () => {
    render(<ShoppingCard result={shoppingResult} />);
    expect(screen.getByText("New")).toBeInTheDocument();
  });

  it("marks a premium seller", () => {
    render(<ShoppingCard result={shoppingResult} />);
    expect(screen.getByTestId("premium")).toBeInTheDocument();
  });

  it("lazy-loads images", () => {
    const { container } = render(<ShoppingCard result={shoppingResult} />);
    expect(container.querySelector("img")).toHaveAttribute("loading", "lazy");
  });
});
