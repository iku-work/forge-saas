import { createFileRoute, notFound } from "@tanstack/react-router";

import { bootstrapModules, getModulesSnapshot } from "@forge/hooks";

import { FilesPage } from "../features/files/FilesPage";

export const Route = createFileRoute("/$locale/_app/files")({
  beforeLoad: async () => {
    await bootstrapModules();
    if (!getModulesSnapshot().fileStorage) {
      throw notFound();
    }
  },
  component: FilesPage,
});
