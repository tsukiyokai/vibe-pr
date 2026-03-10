# Review Reply Templates

Templates for responding to code review comments on CANN PRs.
Used by the review-responder agent.

## Suggestion Accepted

> Done. {description of change}.

Example: "Done. Renamed `tmp_buf` to `recv_buffer` per naming convention."

## Suggestion Accepted with Modification

> Good catch. Applied a slightly different approach: {description}. Reason: {why}.

## Suggestion Declined (with reason)

> Thanks for the suggestion. Keeping the current approach because {reason}. {optional: alternative considered}.

## Question Response

> {Direct answer}. The rationale is {explanation}. See {file:line} for reference.

## Design Concern Response

> This is a good point. {acknowledgment}. The current design choice is based on {reason}. {optional: willing to discuss further / open to alternatives}.

## Tone Guidelines

- Always be respectful and grateful for the review.
- Keep replies concise - one or two sentences.
- Reference specific code locations when relevant.
- If declining a suggestion, always provide a clear technical reason.
- Use Chinese if the reviewer wrote in Chinese, English otherwise.
