# KAT Unity Utilities

Companion to the KAT Blender plugin. Unity 2022.3 or newer. The VRChat SDK is optional.

## What it does

- **KAT only acts when you ask.** Nothing on a model or a setting is changed by itself. The Sync tab has a button, Set selected models up as humanoid, for an FBX that has a `.kat.json` next to it, and the World tab has Set selected up as world or props. Each has a switch to do it on a model's first import instead, off by default.
- **Makes the materials from Blender.** Press Make materials from Blender's textures (Sync tab) on a selected model. The `.kat.json` lists each material's textures. KAT creates the materials in a
  `Materials` folder next to the model and uses them in place of the FBX's own. Materials that already exist are never
  touched. Textures that live outside the project are copied in only after you say yes.
- **The KAT panel.** **KAT > KAT Panel** opens one window with tabs (Avatar, Sync, Optimize, World, Settings), the way the VRChat SDK has its control panel. Everything below is a button in it. Dock it anywhere. Pick the avatar at the top, or let it follow what you select.
- **Send to Blender.** Right-click a model file, a prefab, or an object in the scene and pick **KAT > Send to Blender**.
  It writes `<model>.kat-unity.json` next to the model: which texture each material slot really uses (the materials as
  they are on the prefab or object, so a swapped material comes across), and whether it is opaque, cutout or
  transparent. Import the model in Blender and its materials point at the same files. For a model already open, press
  Sync textures from Unity (Optimize > Textures). Nothing is written until you ask. The Sync tab has a switch,
  Write the sync files for Blender automatically, that brings the old behaviour back.
- **Outfit toggles.** Mark the clothes in Blender's Outfits panel (and make the anti-clip shapes there), export, and
  The Avatar tab's Set up outfit toggles builds the animator layers, the saved bool parameters and an Outfits menu. Where two
  pieces are worn together it also turns on the blend shapes that keep them from clipping. Run it again after
  changing the outfits: what KAT made before is replaced and nothing else is touched.
- **Texture optimizer.** The Optimize tab's Optimize textures of selection caps the import size (2048) of the textures that the
  selected objects, materials or folders use, turns compression on and uses crunch for colour maps. Only import
  settings change, never the files, and running it again changes nothing.
- **Worlds and props.** Export from Blender's World panel and the model comes in ready: collision proxies become
  colliders, static meshes get static flags, pickups get a rigidbody, meshes without a lightmap UV map get one made,
  and Name_LOD0, Name_LOD1 meshes become a LOD group. The World tab has "Make selection static", "Bake occlusion culling"
  and "Report scene cost". With the VRChat Worlds SDK, "Add VRChat pickup to selection" gives props a VRC_Pickup.
- With the VRChat Avatars SDK installed: select your avatar in the scene, then press Set up VRChat avatar in the Avatar tab. It adds the
  avatar descriptor, sets the view position from the eye bones, and fills in the visemes.

## Install

**VRChat Creator Companion or ALCOM:** Settings > Packages > Add Repository, paste
`https://krislyz.github.io/KAT-Tools/index.json` (or open `vcc://vpm/addRepo?url=https://krislyz.github.io/KAT-Tools/index.json`),
then add "KAT Unity Utilities" to your project.

**Unity Package Manager:** Add package from git URL:
`https://github.com/Krislyz/KAT-Tools.git?path=/unity/com.krislyz.kat`
