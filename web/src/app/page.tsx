import { redirect } from "next/navigation";

export default function Home() {
  async function go(formData: FormData) {
    "use server";
    const q = String(formData.get("q") ?? "").trim();
    redirect(`/search?q=${encodeURIComponent(q)}`);
  }

  return (
    <main className="mx-auto grid min-h-[70vh] max-w-xl place-items-center px-4">
      <div className="w-full">
        <h1 className="mb-6 text-center text-3xl font-semibold">Beynunehcheh</h1>
        {/* A plain server-action form, so the home page works with JS off. */}
        <form action={go} role="search">
          <input
            type="search"
            name="q"
            placeholder="Search"
            className="input w-full"
          />
        </form>
      </div>
    </main>
  );
}
