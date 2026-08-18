"use client";

import Link from "next/link";

export default function ErrorPage() {
  return (
    <main className="mx-auto grid min-h-[50vh] max-w-xl place-items-center px-4">
      <div className="text-center">
        <h1 className="text-lg font-semibold">Something went wrong</h1>
        <p className="mt-1 text-sm text-muted">
          The search engine hiccupped. Try again in a moment.
        </p>
        <Link href="/" className="mt-4 inline-block text-sm text-accent underline">
          Back to search
        </Link>
      </div>
    </main>
  );
}
