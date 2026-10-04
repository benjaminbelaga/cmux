import AppKit
import CoreImage

/// Performs one Core Image texture pass before AppKit paints a backdrop.
private struct BackdropTextureRenderer {
    let context: CIContext

    func render(_ source: NSImage, texture: BackdropTexture) -> NSImage? {
        guard texture.filter != .none, texture.strength > 0,
              let tiff = source.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let input = CIImage(bitmapImageRep: bitmap) else { return source }

        let output: CIImage?
        switch texture.filter {
        case .none:
            output = input
        case .orderedDither4x4, .orderedDither8x8:
            output = dither(input, strength: texture.strength,
                            matrixSize: texture.filter == .orderedDither4x4 ? 4 : 8)
        case .halftone:
            output = halftone(input, strength: texture.strength)
        case .grain:
            output = grain(input, strength: texture.strength)
        }
        guard let output,
              let cgImage = context.createCGImage(output, from: input.extent) else { return source }
        return NSImage(cgImage: cgImage, size: source.size)
    }

    private func dither(_ input: CIImage, strength: Double, matrixSize: Int) -> CIImage? {
        guard let filter = CIFilter(name: "CIDither") else { return input }
        filter.setValue(input, forKey: kCIInputImageKey)
        filter.setValue(strength, forKey: "inputIntensity")
        filter.setValue(matrixSize, forKey: "inputMatrixSize")
        return filter.outputImage ?? input
    }

    private func halftone(_ input: CIImage, strength: Double) -> CIImage? {
        guard let filter = CIFilter(name: "CICMYKHalftone") else { return input }
        filter.setValue(input, forKey: kCIInputImageKey)
        filter.setValue(2 + CGFloat(10 * (1 - strength)), forKey: "inputWidth")
        filter.setValue(0, forKey: "inputAngle")
        filter.setValue(0.7 + CGFloat(strength) * 0.3, forKey: "inputSharpness")
        return filter.outputImage?.cropped(to: input.extent) ?? input
    }

    private func grain(_ input: CIImage, strength: Double) -> CIImage? {
        guard let noise = CIFilter(name: "CIRandomGenerator")?.outputImage,
              let color = CIFilter(name: "CIColorMatrix") else { return input }
        color.setValue(noise.cropped(to: input.extent), forKey: kCIInputImageKey)
        let amount = CGFloat(strength * 0.16)
        color.setValue(CIVector(x: 0, y: 0, z: 0, w: amount), forKey: "inputAVector")
        color.setValue(CIVector(x: 0.5, y: 0.5, z: 0.5, w: 0), forKey: "inputBiasVector")
        guard let grain = color.outputImage,
              let blend = CIFilter(name: "CIScreenBlendMode") else { return input }
        blend.setValue(grain, forKey: kCIInputImageKey)
        blend.setValue(input, forKey: kCIInputBackgroundImageKey)
        return blend.outputImage?.cropped(to: input.extent) ?? input
    }
}
