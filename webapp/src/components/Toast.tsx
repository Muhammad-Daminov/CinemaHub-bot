interface Props {
  message: string;
  tone?: "success" | "error";
}

export function Toast({ message, tone = "success" }: Props) {
  return (
    <div
      className={`fixed inset-x-4 bottom-4 z-40 rounded-xl px-4 py-3 text-center text-sm font-medium shadow-lg ${
        tone === "success" ? "bg-marquee text-on-marquee" : "bg-premiere text-on-premiere"
      }`}
    >
      {message}
    </div>
  );
}
