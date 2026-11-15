// Lane C (in Lane D's ui/, HANDOFF): friendly default player names,
// adjective + animal ("Foamy Otter"). Family-friendly lists; every adjective
// is at most 7 characters and every animal at most 8, so any name fits
// PLAYER_NAME_MAX (16) with the space. Pure, tested in playerNames.test.ts.

export const PLAYER_NAME_MAX = 16;

export const ADJECTIVES = [
  "Foamy", "Bouncy", "Cozy", "Sneaky", "Squishy", "Comfy", "Fluffy", "Zippy", "Snappy", "Sunny",
  "Jolly", "Peppy", "Speedy", "Sleepy", "Cheery", "Breezy", "Plucky", "Spongy", "Bubbly", "Wobbly",
  "Giggly", "Dizzy", "Fuzzy", "Lucky", "Nifty", "Quirky", "Silly", "Snug", "Swift", "Happy",
] as const;

export const ANIMALS = [
  "Otter", "Llama", "Panda", "Koala", "Badger", "Beaver", "Bunny", "Duckling", "Ferret", "Gecko",
  "Goose", "Hamster", "Hedgehog", "Kitten", "Lemur", "Marmot", "Moose", "Narwhal", "Owl", "Penguin",
  "Puffin", "Quokka", "Raccoon", "Seal", "Sloth", "Squirrel", "Turtle", "Walrus", "Wombat", "Yak",
] as const;

const pick = <T>(list: readonly T[], r: number): T => list[Math.min(list.length - 1, Math.max(0, Math.floor(r * list.length)))];

/** "Adjective Animal" from two draws of `rand` (default Math.random), at most PLAYER_NAME_MAX characters. */
export function makePlayerName(rand: () => number = Math.random): string {
  const name = `${pick(ADJECTIVES, rand())} ${pick(ANIMALS, rand())}`;
  return name.length <= PLAYER_NAME_MAX ? name : name.slice(0, PLAYER_NAME_MAX);
}
