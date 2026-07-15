import { Scaffold } from "@/app/_components/Scaffold";

export default function BoardPage() {
  return (
    <Scaffold
      surface="Queue board"
      job="Show the next token in numerals legible at 8 meters."
      session="Session 8"
      dark
    />
  );
}
