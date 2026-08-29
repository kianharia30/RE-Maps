import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RE-Maps — property prices on a world map",
  description:
    "Explore residential property prices geographically, through time, from " +
    "official open data. Historical values, current estimates and statistical " +
    "forecasts — with the source and confidence behind every figure.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  // The map owns the viewport; browser UI must not resize it mid-gesture.
  userScalable: false,
  themeColor: "#eef1f5",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="h-full w-full overflow-hidden">{children}</body>
    </html>
  );
}
