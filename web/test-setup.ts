// Gives `bun test` a DOM, so components can be rendered and queried.
import { GlobalRegistrator } from "@happy-dom/global-registrator";

GlobalRegistrator.register({ url: "http://localhost/" });
