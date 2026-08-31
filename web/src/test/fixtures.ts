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
    position_type_label_en: "Permanent", position_type_label_dv: "ދާއިމީ",
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
  ...base, id: 2, source: "other", doc_type: "property",
  title: "Room in Apartment", url: "https://other-source.example/2",
  card: {
    source: "other", hero_image: "https://x/1.jpg", image_count: 4,
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
  ...base, id: 4, source: "other", doc_type: "shopping",
  title: "KICO METAL POWER SUPPLY 24V-5A-120W", url: "https://other-source.example/4",
  card: {
    source: "other", hero_image: "https://x/ps.jpg", image_count: 2,
    title: "KICO METAL POWER SUPPLY 24V-5A-120W",
    price_display: "MVR 850", currency: "MVR", negotiable: false,
    condition: "New", condition_label_en: "New", condition_label_dv: "އާ",
    brand: "KICO", location: "Male",
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
    announcement_type_label_en: "Tender", announcement_type_label_dv: "ބީލަން",
    published_at: "2026-08-01T00:00:00Z",
    external_url: "https://gazette.gov.mv/iulaan/5",
    attachment_count: 2, is_tender: true,
  },
} as ResultOut;

export const dhivehiNewsResult: ResultOut = {
  ...newsResult, id: 7, title: "ބީލަން ހުށަހެޅުއްވުމަށް",
  card: { ...newsResult.card, title: "ބީލަން ހުށަހެޅުއްވުމަށް" },
} as ResultOut;

export const dhivehiTitleResult: ResultOut = {
  ...base, id: 6, title: "ވަޒީފާގެ ފުރުޞަތު", translated: true,
  doc_type: "job", card: { ...jobResult.card, role: "ވަޒީފާގެ ފުރުޞަތު" },
} as ResultOut;

// Real-world shape: enrichment's `role` is English-only, but the query
// resolved this document's title to Dhivehi -- the card must show the
// resolved title, not silently fall back to English (the bug spec 9 exists
// to prevent, just one layer up from title/summary).
export const dhivehiTitleEnglishRoleResult: ResultOut = {
  ...base, id: 8, title: "ލެބޯޓްރީ ޓެކްނީޝަން", translated: false,
  doc_type: "job", card: { ...jobResult.card, role: "Laboratory Technician" },
} as ResultOut;

// A Dhivehi-titled job whose free-text fields carry the _dv siblings
// translate_card_vocab produces -- one qualification deliberately has no
// _dv yet, to exercise the per-item English fallback.
export const dhivehiJobFreeTextResult: ResultOut = {
  ...base, id: 9, title: "ލެބޯޓްރީ ޓެކްނީޝަން", translated: false,
  doc_type: "job",
  card: {
    ...jobResult.card,
    role: "Laboratory Technician",
    employer: "The Maldives National University",
    employer_dv: "ދިވެހިރާއްޖޭގެ ޤައުމީ ޔުނިވަރސިޓީ",
    qualifications: ["A related degree", "Two years experience"],
    qualifications_dv: ["ގުޅުންހުރި ދާއިރާއަކުން ޑިގްރީއެއް", ""],
    required_documents: ["Updated CV"],
    required_documents_dv: ["އަޕްޑޭޓް ކުރެވިފައިވާ ސީވީ"],
    compensation: {
      ...(jobResult.card.compensation as Record<string, unknown>),
      allowances: [{ kind: "attendance", label_raw: "Attendance Allowance",
                    label_dv: "ހާޒިރީ އެލަވަންސް", amount: 4400,
                    basis: "fixed_monthly" }],
    },
    apply_methods: [
      { kind: "form", value: "https://forms.gle/abc", label_en: "Online via form link",
        label_dv: "ފޯމު މެދުވެރިކޮށް" },
    ],
  },
} as ResultOut;
