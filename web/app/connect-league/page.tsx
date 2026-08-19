import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ConnectLeagueForm } from "@/components/connect-league/connect-league-form";
import { getCurrentUser } from "@/lib/auth";
import { getLeagueConnection } from "@/lib/leagueConnection";

export default async function ConnectLeaguePage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const connection = await getLeagueConnection();
  if (connection?.league_id) {
    redirect(connection.team_id ? `/league/${connection.league_id}` : "/connect-league/pick-team");
  }

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">Connect your ESPN league</CardTitle>
        </CardHeader>
        <CardContent>
          <ConnectLeagueForm />
        </CardContent>
      </Card>
    </div>
  );
}
