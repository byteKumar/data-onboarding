import { useState } from "react";
import MappingPage from "./pages/Mapping";

type Tab = "mapping";

export default function App() {
  const [tab, setTab] = useState<Tab>("mapping");
  return (
    <div className="app">
      <header>
        <h1>Data Onboarding</h1>
        <nav>
          <button className={tab === "mapping" ? "active" : ""} onClick={() => setTab("mapping")}>
            Mapping review
          </button>
        </nav>
      </header>
      <main>{tab === "mapping" && <MappingPage />}</main>
    </div>
  );
}
