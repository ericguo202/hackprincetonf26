import { World } from "./world/World";
import { ChatPanel } from "./ui/ChatPanel";
import { Hud } from "./ui/Hud";
import { SessionBoot } from "./ui/SessionBoot";

export default function App() {
  return (
    <div className="app">
      <SessionBoot />
      <World />
      <Hud />
      <ChatPanel />
    </div>
  );
}
