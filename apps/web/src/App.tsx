import { World } from "./world/World";
import { ChatPanel } from "./ui/ChatPanel";

export default function App() {
  return (
    <div className="app">
      <World />
      <ChatPanel />
    </div>
  );
}
