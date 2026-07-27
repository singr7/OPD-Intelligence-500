import { StaffLogin } from "@/app/_components/StaffLogin";

export function Login({ onToken }: { onToken: (token: string) => void }) {
  return (
    <StaffLogin
      role="Administrator"
      description="Manage operations, workforce, clinical content, and system controls."
      defaultPhone="+915550000001"
      onToken={onToken}
    />
  );
}
