/**
 * Search loading skeleton: three grey card outlines. The search route is a
 * server component, so without this a tab switch shows the previous page
 * frozen while the new one renders.
 */
export default function Loading() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-4">
      <div className="h-11 w-full animate-pulse rounded-full bg-base-200" />
      <div className="mt-3 h-9 w-full animate-pulse bg-base-200" />
      <div className="mt-6 grid gap-6 lg:grid-cols-[220px_1fr]">
        <div className="space-y-3">
          <div className="h-24 w-full animate-pulse rounded bg-base-200" />
          <div className="h-24 w-full animate-pulse rounded bg-base-200" />
        </div>
        <div className="space-y-3">
          <div className="h-28 w-full animate-pulse rounded-lg bg-base-200" />
          <div className="h-28 w-full animate-pulse rounded-lg bg-base-200" />
          <div className="h-28 w-full animate-pulse rounded-lg bg-base-200" />
        </div>
      </div>
    </div>
  );
}
