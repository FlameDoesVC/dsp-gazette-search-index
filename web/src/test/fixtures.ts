import type { ResultOut } from "@/lib/api";

const base = {
  id: 1, source: "gazette", url: "https://gazette.gov.mv/iulaan/1",
  title: "Administrative Officer", summary: "A GS3 post.", translated: false,
  score: 1.0,
};

export const jobResult: ResultOut = {
  ...base, doc_type: "job",
  card: {
    source: "gazette",
    role: "Administrative Officer",
    employer: "Ministry of Example",
    salary_display: "MVR 10,750 / month",
    salary_state: "listed",
    net_estimate: {
      value: 14397.5, is_floor: false, working_days: 20, completeness: "full",
      breakdown: [
        { label: "basic", amount: 10750 },
        { label: "pension", amount: -752.5 },
        { label: "attendance", amount: 4400 },
      ],
    },
    compensation: {
      basic_salary: 10750, currency: "MVR", period: "month",
      pension_applies: true, pension_rate: 0.07, salary_state: "listed",
      completeness: "full",
      allowances: [{ kind: "attendance", label_raw: "ހާޒިރީ އެލަވަންސް",
                     amount: 4400, basis: "fixed_monthly" }],
    },
    grade: "GS3", location: "Male", position_type: "Permanent",
    deadline: "2026-08-31", deadline_state: "open",
    apply_kinds: ["form", "email"],
    apply_methods: [
      { kind: "form", value: "https://forms.gle/abc", label_en: "", label_dv: "" },
      { kind: "email", value: "hr@example.gov.mv", label_en: "", label_dv: "" },
    ],
    qualifications: ["Basic medical degree", "Two years experience"],
    required_documents: ["ID card copy", "Accredited certificates"],
    detail_source: "attachment",
  },
} as ResultOut;

export const jobUnlisted: ResultOut = {
  ...jobResult,
  card: { ...jobResult.card, salary_display: "Unlisted", salary_state: "unlisted",
          net_estimate: null },
} as ResultOut;

export const jobNegotiable: ResultOut = {
  ...jobResult,
  card: { ...jobResult.card, salary_display: "Negotiable",
          salary_state: "negotiable", net_estimate: null },
} as ResultOut;

export const jobFloorEstimate: ResultOut = {
  ...jobResult,
  card: {
    ...jobResult.card,
    net_estimate: {
      value: 12000, is_floor: true, working_days: 20,
      completeness: "partial", breakdown: [],
    },
  },
} as ResultOut;

export const propertyRoomOfThree: ResultOut = {
  ...base, id: 2, source: "ibay", doc_type: "property",
  title: "Room in Apartment", url: "https://ibay.com.mv/2",
  card: {
    source: "ibay", hero_image: "https://x/1.jpg", image_count: 4,
    location_display: "Hulhumale Phase 2",
    rent_display: "MVR 7,000 / month", currency: "MVR", currency_inferred: false,
    capacity_display: "1 room of 3, shared",
    unit_kind: "room", is_shared: true,
    bedrooms: 3, bathrooms: 2, furnishing: "Furnished",
    tenant_preference: ["Family"],
  },
} as ResultOut;

export const propertyBedSpace: ResultOut = {
  ...propertyRoomOfThree, id: 3,
  title: "Sharing Bed Space (2 Space)",
  card: { ...propertyRoomOfThree.card, capacity_display: "Bed space, 2 available, shared",
          unit_kind: "bed_space", bedrooms: null, hero_image: null, image_count: 0,
          rent_display: "MVR 2,800 / month", tenant_preference: ["Male", "Working"] },
} as ResultOut;

export const shoppingResult: ResultOut = {
  ...base, id: 4, source: "ibay", doc_type: "shopping",
  title: "KICO METAL POWER SUPPLY 24V-5A-120W", url: "https://ibay.com.mv/4",
  card: {
    source: "ibay", hero_image: "https://x/ps.jpg", image_count: 2,
    title: "KICO METAL POWER SUPPLY 24V-5A-120W",
    price_display: "MVR 850", currency: "MVR", negotiable: false,
    condition: "New", brand: "KICO", location: "Male",
    seller_name: "Kico Store", seller_is_premium: true,
    spec_chips: ["24V", "5A", "120W"],
  },
} as ResultOut;

export const newsResult: ResultOut = {
  ...base, id: 5, doc_type: "news",
  title: "Bids invited for harbour works",
  summary: "The ministry invites sealed bids for harbour construction at Kulhudhuffushi.",
  card: {
    source: "gazette",
    title: "Bids invited for harbour works",
    summary: "The ministry invites sealed bids for harbour construction at Kulhudhuffushi.",
    office: "Ministry of Example", announcement_type: "ބީލަން",
    published_at: "2026-08-01T00:00:00Z",
    external_url: "https://gazette.gov.mv/iulaan/5",
    attachment_count: 2, is_tender: true,
  },
} as ResultOut;

export const dhivehiTitleResult: ResultOut = {
  ...base, id: 6, title: "ވަޒީފާގެ ފުރުޞަތު", translated: true,
  doc_type: "job", card: { ...jobResult.card, role: "ވަޒީފާގެ ފުރުޞަތު" },
} as ResultOut;
