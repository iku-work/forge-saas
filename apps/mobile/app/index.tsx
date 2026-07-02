import { Redirect } from "expo-router";

import { useSession } from "../src/lib/auth";

export default function Index() {
  const { user } = useSession();
  return <Redirect href={user ? "/dashboard" : "/login"} />;
}
