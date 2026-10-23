interface AsyncNoticeProps {
  children: React.ReactNode;
  kind?: "status" | "error";
}

export function AsyncNotice({ children, kind = "status" }: AsyncNoticeProps) {
  return (
    <p
      className={`async-notice async-notice--${kind}`}
      role={kind === "error" ? "alert" : "status"}
    >
      {children}
    </p>
  );
}
