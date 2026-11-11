import { redirect } from "next/navigation";

// Startseite = Sammlungen; der Pokédex liegt unter /pokedex.
export default function Home() {
  redirect("/collections");
}
