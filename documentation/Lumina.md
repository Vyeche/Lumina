Lumina
Based on the provided images, here is a documentation for "Lumina."

# Do not edit or change this file

The Lumina UI combines an interactive 3D viewport with specialized target swatches and adjustment sliders to give artists direct control over simulated color interactions.

**Interactive Viewport & Picker**

* **3D Sphere Canvas:** Displays a real-time sphere rendered with key light, ambient light, and base color calculations.
* **Surface Color Picker Pin:** A draggable target pointer on the sphere surface that lets you sample calculated RGB values—including highlights, midtones, shadow turners, and ambient reflected light—to paint directly onto your canvas.

**Color Target Selectors**

* **Base Color Swatch:** Sets the local, unlit color of the object without changing the light source properties.
* **Light Color Swatch:** Sets the hue, saturation, and intensity of the primary incoming light source.
* **Shadow / Ambient Swatch:** Defines the color tint of ambient bounce light in non-illuminated areas, preventing flat black shadows.

**Adjustment Controls**

* **Hue & Saturation Sliders:** Multi-spectrum slider bars that fine-tune the color properties of whichever target swatch is currently active (Base, Light, or Shadow).
* **Contrast Slider:** Controls the light falloff gradient on the sphere, adjusting the transition sharpness between illuminated areas and shadows (ranging from soft, diffused ambient light to crisp, high-contrast lighting).
* **Panel Navigation Bar:** Bottom control icons for toggling between standard color palettes, lighting sphere modes, and preset configurations.

Implementing the Lumina requires a custom shader or pixel-rendering pipeline, mathematical color mixing, and an interactive viewport with event handling.

**1. Sphere Geometry & Normal Generation**
Render a unit sphere projected onto a 2D square viewport with coordinates $(x, y) \in [-1, 1]^2$.

* **Circle Masking:** For any pixel where $x^2 + y^2 > 1$, discard the pixel or render background transparency.
* **Normal Calculation:** Calculate the 3D surface normal $\mathbf{N}$ at coordinates $(x, y)$:

$$\mathbf{N} = \left(x, y, \sqrt{1 - x^2 - y^2}\right)$$



**2. Lighting Shader & Color Blending Math**
Calculate pixel colors dynamically based on the surface normal, key light direction vector $\mathbf{L}$, ambient light, and base object color.

* **Diffuse Intensity:** Calculate the dot product between normal $\mathbf{N}$ and light direction $\mathbf{L}$:

$$d = \max(0, \mathbf{N} \cdot \mathbf{L})$$


* **Contrast Adjustment:** Remap the lighting falloff $d$ using a contrast factor $k$:

$$I = \text{clamp}\left(d^k, 0, 1\right)$$


* **Color Multiplication Pipeline:** Blend base color ($C_{\text{base}}$), key light color ($C_{\text{light}}$), and shadow/ambient color ($C_{\text{shadow}}$) using element-wise RGB multiplication ($\odot$):

$$C_{\text{pixel}} = C_{\text{base}} \odot \left( C_{\text{light}} \cdot I + C_{\text{shadow}} \cdot (1 - I) \right)$$



**3. Interactive Sampling Engine**

* **Event Listener:** Capture `MouseDown` / `MouseMove` / `Touch` events over the sphere canvas.
* **Coordinate Mapping:** Translate screen pixel coordinates $(px, py)$ into normalized viewport space $(x, y)$.
* **Color Extraction:** Read the calculated RGB value directly from the rendered framebuffer at $(px, py)$ (or re-evaluate the color equation at $(x, y)$) and pass it to the active brush tool.

**4. UI State & Parameter Sliders**

* **Selection State Machine:** Maintain an active state enum `[BASE_COLOR, LIGHT_COLOR, SHADOW_COLOR]`. Tapping a swatch sets the target parameter.
* **HSV / Contrast Sliders:**
* **Hue/Saturation Sliders:** Update the active target's RGB values.
* **Contrast Slider:** Adjusts the exponential lighting factor $k$ in real-time.


* **Redraw Trigger:** Updating any slider updates shader uniforms or triggers a viewport repaint.

# Lumina Documentation

Lumina is an innovative tool designed to help users study and apply realistic lighting in 3D environments. By simulating a sphere illuminated by a controlled light source, it allows users to visualize the complex interactions between light, ambient light, and the base color of an object, providing deep insights into rendering and shading.

## Overview

Lumina is a visualization tool that simulates the physical interaction of light on a 3D object. Its primary function is to help artists and students understand how light wraps around a surface, affecting its color, texture, and depth.

Key features include:

* Simulating a sphere illuminated by a controllable light source.
* Visualizing the interaction between the light, the ambient light, and the base color of the object.
* Providing real-time control over lighting parameters through a dedicated interface.

## Conceptual Diagram (Visualizing Illumination)

As detailed in the documentation, the principles of illumination are broken down into key components:

* The Main Light: The primary, controlled light source that sculpts the form of the object.
* Ambient and Reflected Light: Light that fills in the object's shadows and contributes to the overall base color, regardless of the main light position.
* Shadows in Real-World vs. Artistic Black: The tool allows users to distinguish between mathematically calculated shadows and artistic values, enabling nuanced control over depth and form.

## User Interface and Control Panel

The application features a sophisticated interface that allows users to manipulate lighting parameters with precision.

### 1. Main Display Area

The central part of the screen displays the 3D sphere (the Sphere) being lit. Users can observe how changes to the settings immediately affect the rendered image. The UI includes:

* A color picker for setting the base color of the object.
* A Color Sphere used for managing the light source color and intensity.
* Visual sliders and indicators to control various lighting effects.

### 2. Lighting and Color Controls

The control panel is divided into several sections to manage different aspects of the lighting simulation:

#### Color Controls

* The Base Color: Defines the fundamental color of the object.
* The Color of the Shadow: Controls the color specifically applied in the shaded areas.
* The Color of the Highlight: Controls the color applied where the main light hits the surface directly.

#### Intensity and Environment Controls

* Intensity Sliders: Allows for fine-tuning the brightness of the primary light source.
* Ambient/Reflected Light Controls: Enables adjustment of ambient occlusion and reflection, which helps define the object's form even in shadow.
* Contrast Slider: Provides a way to boost or reduce the difference between the light and shadow areas.

### 3. Advanced Parameters

Users can delve deeper into the physics-based rendering by adjusting:

* Mixer/Mode: Controls how the various light components (base, shadow, highlight) are blended together.
* Glow Settings: Includes a dedicated section for controlling the "Glow" effect, which simulates light bloom or the emission properties of the surface.

## Learning Objectives

By using Lumina, users can achieve the following:

1. Understand Light Wrap: See how light transitions from the highlight, through the mid-tones, into the shadow areas.
2. Control Form and Volume: Manipulate the contrast and shadows to make objects appear three-dimensional and tangible.
3. Master Color Theory in Rendering: Learn how different light sources and ambient conditions alter the perceived color of an object, moving beyond flat colors to rich, detailed surfaces.

## UI/UX Layout Specifications: "Lumina"

This document provides a structural breakdown of the user interface (UI) for "Lumina," based on the visual information from the provided images.

---

### 1. Layout Philosophy & Structure

The layout should be built around a Main Workspace and a Control Panel System. The interface needs to feel professional, focused, and highly technical, reminiscent of a digital art suite (e.g., Blender, Substance Painter).

Structure: Three main sections:

1. Canvas (Main Display): The large central area dedicated to displaying the 3D Sphere.
2. Control Panel (Right/Sidebar): A persistent, collapsible area containing all parameters and sliders.
3. Context/Help Panel (Bottom/Footer): Displaying instructional or status information.

### 2. Workspace Breakdown (Conceptual View)

| Section           | Size/Placement   | Primary Function | Content Details                                               |
|-------------------|------------------|------------------|---------------------------------------------------------------|
| **Canvas**        | 65-70% of screen | Visual Output    | Displays the 3D Sphere, rendered with current lighting settings. |
| **Control Panel** | 30-35% of screen | Parameter Input  | Contains all sliders, pickers, and color controls.            |
| **Context/Tools** | Top Right Corner | Tooling          | Menu icons (e.g., Settings, Save, Toggle modes).              |

### 3. Detailed Component Specifications

#### A. Canvas Area (The Sphere Display)

* Content: A dynamically rendered sphere (The Sphere).
* Interaction: Must support user interaction (rotation, panning) of the object or camera around the Sphere.
* Visual Aid: A subtle overlay or diagrammatic elements showing the "Principles of Illumination" when in a specific learning mode.

#### B. Control Panel (The Settings)

The Control Panel should be highly structured, utilizing grouping and clear visual hierarchy.

A. Top Level: Color Controls (Grouped Section)

* Base Color Picker: Standard HSL/RGB color wheel with "Base Color" label.
* Highlight Color: Input for "The color of the highlight" (Color picker).
* Shadow Color: Input for "The color of the shadow" (Color picker).

B. Mid Level: Lighting Intensity Controls (Grouped Section)

* Main Light Intensity: Slider with numerical read-out, labeled "Main Light".
* Ambient/Reflected Light: Controls for "Ambient and reflected light" (Sliders/Color controls).
* Contrast Slider: A wide-range slider for the overall contrast ratio.

C. Bottom Level: Advanced Modifiers (Grouped Section)

* Mix Mode: A dropdown or set of segmented controls (e.g., "Additive," "Multiplicative," "Blended").
* Glow/Bloom: Specific sliders and intensity controls for post-processing glow effects.
* Export/View Toggles: Buttons to switch between "Artistic Mode" and "Real-World Mode" views.

D. Navigation & View Toggles (Right/Top Right-most)

* Back/Next Navigation: Left/Right arrow icons for tutorial/flow.
* Settings: Gear icon for general preferences.

### 4. Interaction & State Management

* Real-time Feedback: All slider movements and color changes must reflect immediately (within milliseconds) in the Canvas area.
* State Preservation: When the user navigates between tutorials or sections, the last-saved color/lighting states should be preserved unless explicitly reset.
* Visual State Indicators: Small dots or icons should clearly indicate which "lighting principles" are currently active (e.

### 5. Design Style Guide Notes

* Aesthetic: Technical / Industrial-Professional. Dark mode is preferred to allow the rendered Sphere to be the focus point of light.
* Typography: Sans-serif, clean, and highly legible (e.g., Roboto, Montserrat).
* Spacing: Utilize generous negative space around controls to prevent "cluttering."
* Color Palette: Dominated by dark greys/blacks, with accent colors derived from the Sphere's highlights or primary light source.

To effectively build "Lumina," the code needs to manage a high-frequency real-time rendering and input-output synchronization.

Here is a functional breakdown of what the underlying code must accomplish:

---

## Technical Functionality Requirements

The core purpose of the code must be to act as a real-time parameter-driven rendering engine.

### 1. Real-Time Rendering Engine (The Heart)

The code must manage a 3D3D rendering loop (using WebGL, Three.js, Babylon.js, or a native engine like Unity/Unreal) that renders the Sphere.

* Object Representation: The Sphere is a geometrically defined sphere mesh.
* Lighting Math: The code must implement PBRDFloat shaders or PBR (Physically Based Rendering) shaders. This is crucial because the lighting isn't just "brighten light," but how light interacts with surface properties (albedo, roughness, metallic, and the custom ambient occlusion).
* Camera Control: Code must manage a camera object whose position and focal point are synchronized with user inputs to allow for inspection of the Sphere detail.

### 2. Input Handling (The Bridge)

The code needs logic to map all UI elements (sliders, pickers, buttons) to their respective rendering engine properties.

| UI Component            | Data Type       | Technical Action Required                                                                                                 |
|-------------------------|-----------------|---------------------------------------------------------------------------------------------------------------------------|
| **Base Color Picker**   | RGBA values     | Set the `base_color` or `albedo` parameter on the Sphere material.                                                           |
| **Highlight Slider**    | Float (0.0-1.0) | Modulate the intensity or weight of the calculated highlight component.                                                   |
| **Shadow Color Picker** | RGBA values     | Set the specific `shadow_color` parameter (likely modulating occlusion/environment map).                                  |
| **Intensity Slider**    | Float (0.0-5.0) | Increase the Light Source "Power" or "Luminance" input for the PBRDF calculation.                                         |
| **Contrast Slider**     | Float           | Adjust the exposure or tone-mapping curve applied to the final rendered image buffer.                                     |
| **Mixer Mode Selector** | Enum/String     | Change which shader program is active in the rendering loop (e.g., switch from `Mode_Additive` to `Mode_Multiplicative`). |
| **Glow/high controls**  | Boolean/Int     | Toggle or blend shader features (e.g., Bloom, Fresnel effects) that are not standardly on.                                |

### 3. Simulation Logic (The Physics)

The code must handle the mathematical interaction between these three core components:

1. Light Source ($L$): Defined by position, color, and intensity.
2. Object Surface ($S$s$$): Defined by the Base Color and material properties.
3. Environment ($E$): Defined by the Ambient Light settings.

The final rendered pixel color ($C_{final}$) must be a function of these three elements. The code must manage the blending these inputs:

$$
C_{final} = f(S, L, E)
$$

Example: The code must calculate the diffuse bounce off the main light, then add the ambient light, and finally apply a contrast mapping to it all.

### 4. State Management

The code must persist the state across interactions.

* Persistence: Any change in UI sliders must immediately update the rendering loop variables.
* 
* History (Optional but helpful): The code could maintain a simple buffer to allow users to step back through the various parameter states they have set during a session.

---

## Technology Stack Recommendations

For a modern, performant version of this tool, the code should leverage:

* Shaders: Custom GLSL/GLSL code will be necessary to implement the "Principle of Illumination" math accurately, as it requires granular control over how light is calculated per pixel.
## Plugin Development Documentation

See the following documentation files for detailed information:

- **[GettingStarted.md](GettingStarted.md)** - install, enable, and a walkthrough
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - module layout and the shading maths
- **[KritaPluginPatterns.md](KritaPluginPatterns.md)** - Krita docker creation patterns
- **[Bugs.md](Bugs.md)** - what has broken, and the fixes

> This document is the original design brief, kept as written. Two earlier
> fix summaries it used to point at have since been removed as superseded;
> their content is covered by `Bugs.md`.

## Quick Start

1. Enable the plugin in Krita: Settings → Configure Krita → Python Plugin Manager
2. Find "Lumina" and check the enable checkbox
3. The docker will appear on the right side of the interface
