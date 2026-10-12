import { pictureOf } from "../account.tsx";
import type { SponsorPublic } from "../types.ts";
import { Icon } from "./Brand.tsx";
import { Avatar } from "./ui.tsx";

/** What to call a sponsor: their name, their company, their GitHub handle, or neither. */
export function sponsorName(sponsor: SponsorPublic | null): string {
  return sponsor?.name ?? sponsor?.company ?? (sponsor?.github ? `@${sponsor.github}` : "A private sponsor");
}

/** Who lists a bounty. One line on a card; with their links on the bounty's own page. */
export function SponsoredBy({ sponsor, full = false }: { sponsor: SponsorPublic | null; full?: boolean }) {
  const name = sponsorName(sponsor);
  const picture = sponsor ? pictureOf(sponsor) : null;
  if (!full) {
    return (
      <p className="sponsored">
        <Avatar name={sponsor?.name ?? sponsor?.company ?? null} picture={picture} size={20} />
        <span>
          by <b>{name}</b>
        </span>
      </p>
    );
  }
  return (
    <div className="sponsor-card">
      <Avatar name={sponsor?.name ?? sponsor?.company ?? null} picture={picture} size={44} />
      <span className="sponsor-who">
        <b>{name}</b>
        {sponsor?.company && sponsor.company !== name && <small>{sponsor.company}</small>}
        <span className="sponsor-links">
          {sponsor?.github && (
            <a href={`https://github.com/${sponsor.github}`} target="_blank" rel="noreferrer">
              <Icon name="github" size={13} /> {sponsor.github}
            </a>
          )}
          {sponsor?.website && (
            <a href={sponsor.website} target="_blank" rel="noreferrer">
              <Icon name="globe" size={13} /> {new URL(sponsor.website).host}
            </a>
          )}
        </span>
      </span>
    </div>
  );
}
