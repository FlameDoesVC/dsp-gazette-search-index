"""Real strings from the corpus. Spec 4.3.1, 4.4, 5.2.

Do not paraphrase these. They are the specific shapes the extractors have to
survive, and several of them (the '/-' suffix, the seven-digit number embedded
in a spec title) are the reason a rule exists.
"""

# --- titles from a prior marketplace source that carry their whole spec sheet ---
POWER_SUPPLY_TITLE = "KICO METAL POWER SUPPLY 24V-5A-120W / 7884445"
ROOM_TITLE = "1 Room Apartment for rent Viber Only 9223232 7000/- Near IGMH"
BEDSPACE_TITLE = (
    "Sharing Bed Space (2 Space) Available Prefer South Indian Boy (Tamil) 2800"
)
SHARED_HOUSE_TITLE = "Vazeefaa ah dhaa firihen kudhin bahattaden (phase 2)"

# --- gazette job body, table-flattened by P3 ---
GAZETTE_JOB_BODY = """\
މަޤާމް: އެޑްމިނިސްޓްރޭޓިވް އޮފިސަރ
މަޤާމުގެ ގްރޭޑް: GS3
އަސާސީ މުސާރަ: މަހަކު 10,750 ރުފިޔާ
އެލަވަންސް/އިނާޔަތްތައް: ހާޒިރީ އެލަވަންސްގެ ގޮތުގައި މަހަކު 4,400 ރުފިޔާ
ސަރވިސް އެލަވަންސް: މަހަކު 2,000 ރުފިޔާ
ވަޒީފާއަށް އެންމެ ޤާބިލު ފަރާތެއް ހޮވުމަށް ބެލޭނެ ކަންތައްތައް
ސުންގަޑި: 2026 އޯގަސްޓް 31
އީމެއިލް: hr@example.gov.mv ފޯނު: 3323838
"""

# --- an ad that states negotiability, and one that simply omits salary ---
NEGOTIABLE_BODY = "Salary negotiable depending on experience. Call 7994400."
NO_SALARY_BODY = "Looking for a cashier. Call 9483252 for details."

# --- marketplace ProductInfo values that arrive as strings, not numbers ---
INFO_BEDROOMS = {"Bedrooms": "3 Rooms", "Bathrooms": "2", "Ideal Tenants": "Family"}
INFO_BEDROOMS_PLUS = {"Bedrooms": "4 Rooms and More"}
INFO_FACILITIES = {"Room Facilities": "Air Conditioning, Fans, Towels"}
INFO_BRAND_ALIAS = {"Brand": "Apple (iPhone)"}

# --- money written four ways, all of which appear ---
MONEY_STRINGS = ["10,750", "-/32,632", "7000/-", "MVR 5,000", "USD 450", "$450"]
