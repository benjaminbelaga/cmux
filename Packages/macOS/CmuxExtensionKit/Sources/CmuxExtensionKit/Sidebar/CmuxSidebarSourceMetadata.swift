import Foundation
import CryptoKit

/// Bounded navigation metadata. These values never authenticate a mailbox or
/// authorize opening a resource; the host must revalidate its source binding.
public struct CmuxSidebarSourceReference: Codable, Equatable, Sendable {
    public var provider: String
    public var accountRef: String
    public var directoryUserId: String
    public var resourceId: String
    public var messageId: String?
    public var evidenceFingerprint: String

    public init(provider: String = "gmail", accountRef: String, directoryUserId: String,
                resourceId: String, messageId: String? = nil, evidenceFingerprint: String) {
        self.provider = provider
        self.accountRef = accountRef
        self.directoryUserId = directoryUserId
        self.resourceId = resourceId
        self.messageId = messageId
        self.evidenceFingerprint = evidenceFingerprint
    }

    public var isStructurallyValid: Bool {
        provider == "gmail" && Self.matches(accountRef, "^[A-Za-z0-9_.-]{1,200}$")
            && Self.matches(directoryUserId, "^[0-9]{1,200}$")
            && Self.matches(resourceId, "^GTK-[0-9a-f]{64}$")
            && (messageId == nil || Self.matches(messageId!, "^[A-Za-z0-9_.-]{1,200}$"))
            && Self.matches(evidenceFingerprint, "^[0-9a-f]{64}$")
    }

    public var fingerprint: String? {
        guard isStructurallyValid else { return nil }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(self) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case provider, accountRef, directoryUserId, resourceId, messageId, evidenceFingerprint
    }

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.container(keyedBy: SourceMetadataKey.self)
        guard Set(raw.allKeys.map(\.stringValue)).isSubset(of: Set(CodingKeys.allCases.map(\.rawValue))) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Unexpected source metadata field"))
        }
        let values = try decoder.container(keyedBy: CodingKeys.self)
        provider = try values.decode(String.self, forKey: .provider)
        accountRef = try values.decode(String.self, forKey: .accountRef)
        directoryUserId = try values.decode(String.self, forKey: .directoryUserId)
        resourceId = try values.decode(String.self, forKey: .resourceId)
        messageId = try values.decodeIfPresent(String.self, forKey: .messageId)
        evidenceFingerprint = try values.decode(String.self, forKey: .evidenceFingerprint)
        guard isStructurallyValid else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Invalid bounded source metadata"))
        }
    }

    static func matches(_ value: String, _ expression: String) -> Bool {
        value.range(of: expression, options: .regularExpression) != nil
    }
}

/// Native-owned mapping to a retained CaseOS request. The resolver still checks
/// its server capsule, Directory identity and fresh provider evidence on each use.
public struct CmuxSidebarSourceRequestBinding: Codable, Equatable, Sendable {
    public var requestId: String
    public var sourceReferenceFingerprint: String
    public var attachedRevision: UInt64

    public init(requestId: String, sourceReferenceFingerprint: String, attachedRevision: UInt64) {
        self.requestId = requestId
        self.sourceReferenceFingerprint = sourceReferenceFingerprint
        self.attachedRevision = attachedRevision
    }

    public var isStructurallyValid: Bool {
        CmuxSidebarSourceReference.matches(requestId, "^CEO-[A-Za-z0-9][A-Za-z0-9_.-]{0,123}$")
            && CmuxSidebarSourceReference.matches(sourceReferenceFingerprint, "^[0-9a-f]{64}$")
    }
}

/// Optional fields preserve decoding of existing workspace payloads. Producers
/// validate this bound before persisting or forwarding any of these records.
public struct CmuxSidebarSourceMetadata: Codable, Equatable, Sendable {
    public var sourceReferences: [CmuxSidebarSourceReference]?
    public var serviceObservations: [CmuxSidebarServiceObservation]?
    public var sourceRequestBindings: [CmuxSidebarSourceRequestBinding]?

    public init(sourceReferences: [CmuxSidebarSourceReference]? = nil,
                serviceObservations: [CmuxSidebarServiceObservation]? = nil,
                sourceRequestBindings: [CmuxSidebarSourceRequestBinding]? = nil) {
        self.sourceReferences = sourceReferences
        self.serviceObservations = serviceObservations
        self.sourceRequestBindings = sourceRequestBindings
    }

    public var isStructurallyValid: Bool {
        let references = sourceReferences ?? []
        let observations = serviceObservations ?? []
        let bindings = sourceRequestBindings ?? []
        let fingerprints = references.compactMap(\.fingerprint)
        return references.count <= 32 && observations.count <= 32 && bindings.count <= 32
            && references.allSatisfy(\.isStructurallyValid)
            && fingerprints.count == references.count && Set(fingerprints).count == fingerprints.count
            && observations.allSatisfy { $0.service == "gmail" }
            && bindings.allSatisfy(\.isStructurallyValid)
            && Set(bindings.map(\.requestId)).count == bindings.count
            && Set(bindings.map(\.sourceReferenceFingerprint)).count == bindings.count
            && Set(bindings.map(\.sourceReferenceFingerprint)).isSubset(of: Set(fingerprints))
    }

    private enum CodingKeys: String, CodingKey { case sourceReferences, serviceObservations, sourceRequestBindings }

    public init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sourceReferences = try values.decodeIfPresent([CmuxSidebarSourceReference].self, forKey: .sourceReferences)
        serviceObservations = try values.decodeIfPresent([CmuxSidebarServiceObservation].self, forKey: .serviceObservations)
        sourceRequestBindings = try values.decodeIfPresent([CmuxSidebarSourceRequestBinding].self, forKey: .sourceRequestBindings)
        guard isStructurallyValid else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Source metadata exceeds its bound or has conflicting references"))
        }
    }
}

/// A service observed on an exact native surface, separate from agent identity.
public struct CmuxSidebarServiceObservation: Codable, Equatable, Sendable {
    public enum Kind: String, Codable, Sendable { case browserOrigin = "browser-origin", gmailTool = "gmail-tool" }
    public var service: String
    public var surfaceId: UUID
    public var kind: Kind
    public var observedAt: Date

    public init(surfaceId: UUID, kind: Kind, observedAt: Date) {
        self.service = "gmail"
        self.surfaceId = surfaceId
        self.kind = kind
        self.observedAt = observedAt
    }

    public static func browser(url: URL?, surfaceId: UUID, observedAt: Date) -> Self? {
        guard let url, url.scheme?.lowercased() == "https", url.host?.lowercased() == "mail.google.com",
              url.user == nil, url.password == nil, url.port == nil || url.port == 443 else { return nil }
        return .init(surfaceId: surfaceId, kind: .browserOrigin, observedAt: observedAt)
    }

    public func isCurrent(for surfaceId: UUID, now: Date, maximumAge: TimeInterval = 12) -> Bool {
        service == "gmail" && self.surfaceId == surfaceId && maximumAge >= 0
            && (0...maximumAge).contains(now.timeIntervalSince(observedAt))
    }

    private enum CodingKeys: String, CodingKey, CaseIterable { case service, surfaceId, kind, observedAt }

    public init(from decoder: any Decoder) throws {
        let raw = try decoder.container(keyedBy: SourceMetadataKey.self)
        guard Set(raw.allKeys.map(\.stringValue)) == Set(CodingKeys.allCases.map(\.rawValue)) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Unexpected service observation field"))
        }
        let values = try decoder.container(keyedBy: CodingKeys.self)
        service = try values.decode(String.self, forKey: .service)
        surfaceId = try values.decode(UUID.self, forKey: .surfaceId)
        kind = try values.decode(Kind.self, forKey: .kind)
        observedAt = try values.decode(Date.self, forKey: .observedAt)
        guard service == "gmail" else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Unknown observed service"))
        }
    }
}

private struct SourceMetadataKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { return nil }
}
