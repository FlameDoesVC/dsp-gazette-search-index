import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto grid min-h-[50vh] max-w-xl place-items-center px-4">
      <div className="text-center">
        <h1 className="text-lg font-semibold">Not found</h1>
        <p className="mt-1 text-sm text-base-content/60">
          That page does not exist.
        </p>
        <Link href="/" className="mt-4 inline-block text-sm text-primary underline">
          Back to search
        </Link>
      </div>
    </main>
  );
}
