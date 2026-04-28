# Content Library

Drop images, videos, and music here before running the pipeline.
The pipeline auto-detects the topic and loads the matching folder.

## Folder Structure

```
content/
  <topic>/
    images/    <- .jpg .jpeg .png .webp .jfif
    videos/    <- .mp4 .mov  (pure scene video or b-roll)
    music/
      documentary_long.mp3   <- background music for long video
      documentary_short.mp3  <- background music for short clip
  _shared/
    images/    <- used for every topic as supplement
    videos/
    music/
```

## Supported Topics

| Folder | Matched keywords |
|---|---|
| mindhunter | mindhunter |
| al_capone | al capone, capone |
| pablo_escobar | pablo escobar, escobar, narcos |
| frank_lucas | frank lucas, american gangster |
| charles_manson | charles manson, manson |
| ed_kemper | ed kemper, kemper |
| dahmer | dahmer, jeffrey dahmer |
| ted_bundy | ted bundy, bundy |
| griselda | griselda |
| scarface | scarface |
| goodfellas | goodfellas |

## Priority

1. Topic-specific folder (content/<topic>/)
2. Shared folder (content/_shared/) - added as supplement
3. Telegram uploads - merged after GitHub content

## Video Captions

Name your video files descriptively - the filename becomes the caption:
- ed_kemper_interview_1984.mp4 -> tagged as pure/interview video
- santa_cruz_broll.mp4 -> treated as b-roll
