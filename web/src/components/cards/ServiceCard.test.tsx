import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ResultOut } from "@/lib/api";
import { shoppingResult } from "@/test/fixtures";
import { ProfileNote, SimilarCount } from "./EntityMeta";
import { ResultCard } from "./ResultCard";
import { ServiceCard } from "./ServiceCard";
import { ShoppingCard } from "./ShoppingCard";

const serviceResult: ResultOut = {
  id: 100,
  source: "ibay",
  url: "https://ibay.com.mv/100",
  title: "Electrician",
  summary: "House wiring, light fitting and safety checks.",
  doc_type: "shopping",
  translated: false,
  score: 1.0,
  card: {
    entity_id: 15052,
    kind: "service",
    profile_tier: "consensus",
    inferred_count: 3,
    field_count: 11,
    listing_count: 238,
    services_offered: [
      "Room light board installation",
      "Fan regulator wiring",
      "AC servicing",
      "Power socket wiring",
      "Earth leakage testing",
      "Solar inverter install",
      "Extra service past the cap",
    ],
    coverage: ["Male", "Hulhumale", "Villingili"],
    rate_basis: "per_job",
    call_out: "yes",
    phone: "7438649",
    title: "Electrician",
    summary: "House wiring, light fitting and safety checks.",
  },
} as ResultOut;

const thaanaService: ResultOut = {
  ...serviceResult,
  title: "ކަރަންޓް މަސައްކަތް",
  card: { ...serviceResult.card, title: "ކަރަންޓް މަސައްކަތް" },
} as ResultOut;

describe("ServiceCard", () => {
  it("renders services_offered badges and coverage, but no price or condition", () => {
    const { container } = render(<ServiceCard result={serviceResult} />);
    expect(screen.getByText("Room light board installation")).toBeInTheDocument();
    expect(screen.getByText("Fan regulator wiring")).toBeInTheDocument();
    expect(screen.getByText("Male")).toBeInTheDocument();
    expect(screen.getByText("Hulhumale")).toBeInTheDocument();
    expect(screen.queryByText(/MVR|Price on request/i)).toBeNull();
    expect(screen.queryByText(/New/i)).toBeNull();
    expect(screen.queryByText(/negotiable/i)).toBeNull();
    expect(screen.queryByText(/Seller:/i)).toBeNull();
    expect(screen.queryByText("24V")).toBeNull();
  });

  it("caps services_offered at six", () => {
    render(<ServiceCard result={serviceResult} />);
    expect(screen.getByText("Solar inverter install")).toBeInTheDocument();
    expect(screen.queryByText("Extra service past the cap")).toBeNull();
  });

  it("shows the phone as a tel: link and the rate basis label", () => {
    render(<ServiceCard result={serviceResult} />);
    const tel = screen.getAllByRole("link").find((a) => a.getAttribute("href")?.startsWith("tel:"));
    expect(tel).toHaveAttribute("href", "tel:7438649");
    expect(screen.getByText("Per job")).toBeInTheDocument();
  });

  it("renders provenance affordances from the entity fields", () => {
    render(<ServiceCard result={serviceResult} />);
    expect(screen.getByText("238 similar listings")).toBeInTheDocument();
    expect(screen.getByText("3 of 11 details from model knowledge")).toBeInTheDocument();
  });

  it("gives a Thaana service title its own dir while a Latin coverage stays ltr", () => {
    render(<ServiceCard result={thaanaService} />);
    expect(screen.getByText("ކަރަންޓް މަސައްކަތް")).toHaveAttribute("dir", "rtl");
    expect(screen.getByText("Male")).toHaveAttribute("dir", "ltr");
  });
});

describe("ResultCard routing", () => {
  it("routes a card.kind === 'service' result to ServiceCard despite doc_type 'shopping'", () => {
    render(<ResultCard result={serviceResult} />);
    expect(screen.getByText("Room light board installation")).toBeInTheDocument();
    expect(screen.queryByText(/MVR/)).toBeNull();
    expect(screen.queryByText("24V")).toBeNull();
  });

  it("keeps a product doc_type 'shopping' result on ShoppingCard", () => {
    render(<ResultCard result={shoppingResult} />);
    expect(screen.getByText("MVR 850")).toBeInTheDocument();
    expect(screen.getByText("24V")).toBeInTheDocument();
    expect(screen.queryByText(/similar listings/)).toBeNull();
  });
});

describe("EntityMeta", () => {
  it("SimilarCount renders nothing for a count of 1", () => {
    const { container } = render(<SimilarCount count={1} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("SimilarCount renders 'N similar listings' for 238", () => {
    render(<SimilarCount count={238} />);
    expect(screen.getByText("238 similar listings")).toBeInTheDocument();
  });

  it("ProfileNote renders nothing when inferred_count is 0", () => {
    const { container } = render(
      <ProfileNote tier="grounded" inferredCount={0} fieldCount={11} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("ProfileNote renders 'N of M details from model knowledge' when inferred_count is 3", () => {
    render(<ProfileNote tier="consensus" inferredCount={3} fieldCount={11} />);
    expect(screen.getByText("3 of 11 details from model knowledge")).toBeInTheDocument();
  });
});

describe("ShoppingCard entity affordances", () => {
  it("renders similar listing and profile notes from the card", () => {
    const rich: ResultOut = {
      ...shoppingResult,
      card: {
        ...shoppingResult.card,
        listing_count: 238,
        inferred_count: 3,
        field_count: 11,
        profile_tier: "consensus",
      },
    } as ResultOut;
    render(<ShoppingCard result={rich} />);
    expect(screen.getByText("238 similar listings")).toBeInTheDocument();
    expect(screen.getByText("3 of 11 details from model knowledge")).toBeInTheDocument();
  });
});
