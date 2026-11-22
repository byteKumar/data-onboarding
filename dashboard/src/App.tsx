import { useState } from "react";
import MappingPage from "./pages/Mapping";
import OverviewPage from "./pages/Overview";

type Tab = "overview" | "mapping";

export default function App() {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div className="app">
      <header>
        <h1>Data Onboarding</h1>
        <nav>
          <button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
            Overview
          </button>
          <button className={tab === "mapping" ? "active" : ""} onClick={() => setTab("mapping")}>
            Mapping review
          </button>
        </nav>
      </header>
      <main>{tab === "overview" ? <OverviewPage /> : <MappingPage />}</main>
    </div>
  );
}
