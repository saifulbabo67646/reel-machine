// Which word is being spoken at t (seconds, shot-local).
export function captionState(words, t) {
  let active = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].start <= t) active = i;
    else break;
  }
  const speaking = active >= 0 && t <= words[active].end + 0.25;
  return { active, speaking };
}
