import { getMeta } from "@/lib/api";
import { MetaProvider } from "@/components/MetaProvider";
import "./globals.css";

export const metadata = { title: "Beynunehcheh" };

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const meta = await getMeta();
  return (
    // The document is LTR. Dhivehi content flips per element (spec 10); the
    // chrome language is a separate concern handled by the toggle.
    <html lang="en" dir="ltr">
      <body className="bg-bg text-fg antialiased">
        <MetaProvider meta={meta}>{children}</MetaProvider>
      </body>
    </html>
  );
}
