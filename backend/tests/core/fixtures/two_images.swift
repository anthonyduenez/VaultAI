// Write a HEIC holding two top-level images, with the second one marked primary.
// Usage: swift two_images.swift <first.png> <second.png> <out.heic>
import Foundation
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
func load(_ path: String) -> CGImage {
    let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil)!
    return CGImageSourceCreateImageAtIndex(src, 0, nil)!
}
let out = URL(fileURLWithPath: args[3]) as CFURL
let dest = CGImageDestinationCreateWithURL(out, UTType.heic.identifier as CFString, 2, nil)!
CGImageDestinationSetProperties(dest, [kCGImagePropertyPrimaryImage: 1] as CFDictionary)
CGImageDestinationAddImage(dest, load(args[1]), nil)
CGImageDestinationAddImage(dest, load(args[2]), nil)
print(CGImageDestinationFinalize(dest))
