import { redirect } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TeamPickerForm } from "@/components/connect-league/team-picker-form";
import { getCurrentUser } from "@/lib/auth";
import { getLeagueConnection } from "@/lib/leagueConnection";

export default async function PickTeamPage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");

  const connection = await getLeagueConnection();
  if (!connection?.league_id) redirect("/connect-league");
  if (connection.team_id) redirect(`/league/${connection.league_id}`);

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-12">
      <Card>
        <CardHeader>
          <CardTitle className="font-heading text-xl">Pick your team</CardTitle>
        </CardHeader>
        <CardContent>
          <TeamPickerForm leagueId={connection.league_id} teams={connection.teams} />
        </CardContent>
      </Card>
    </div>
  );
}
